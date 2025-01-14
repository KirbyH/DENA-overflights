import datetime as dt
import math
from typing import Iterable, List, Optional, Tuple, TYPE_CHECKING

import geopandas as gpd
import numpy as np
import rasterio
from osgeo import gdal
from scipy import interpolate
from shapely.geometry import Point

if TYPE_CHECKING:
    from nps_active_space.utils.models import Microphone, Nvspl, Tracks


__all__ = [
    'audibility_to_interval',
    'ambience_from_nvspl',
    'ambience_from_raster',
    'audible_time_delay',
    'build_src_point_mesh',
    'calculate_duration_summary',
    'climb_angle',
    'compute_fbeta',
    'contiguous_regions',
    'coords_to_utm',
    'create_overlapping_mesh',
    'interpolate_spline',
    'NMSIM_bbox_utm',
    'project_raster'
]


def NMSIM_bbox_utm(study_area: gpd.GeoDataFrame) -> str:
    """
    NMSIM references an entire project to the westernmost extent of the elevation (or landcover) file.
    Given that, return the UTM Zone the project will eventually use. NMSIM uses NAD83 as its geographic
    coordinate system, so the study area will be projected into NAD83 before calculating the UTM zone.

    Parameters
    ----------
    study_area : gpd.GeoDataFrame
        A study area (Polygon) to find the UTM zone of the westernmost extent for.

    Returns
    -------
    UTM zone projection name (e.g.  'epsg:26905' for UTM 5N) that aligns with the westernmost extent of a study area.
    """
    if study_area.crs.to_epsg() != 4269:
        study_area = study_area.to_crs(epsg='4269')
    study_area_bbox = study_area.geometry.iloc[0].bounds  # (minx, miny, maxx, maxy)
    lat = study_area_bbox[3]  # maxy
    lon = study_area_bbox[0]  # minx

    return coords_to_utm(lat, lon)


def coords_to_utm(lat: float, lon: float) -> str:
    """
    Takes the latitude and longitude of a point and outputs the EPSG code corresponding to the UTM zone of the point.

    Parameters
    ----------
    lat : float
        Latitude of a point in decimal degrees in a geographic coordinate system.
    lon : float
        Longitude of a point in decimal degrees in a geographic coordinate system.

    Returns
    -------
    utm_proj : str
        UTM zone projection name (e.g.  'epsg:26905' for UTM 5N)

    Notes
    -----
    Remember: x=longitude, y=latitude
    """
    # 6 degrees per zone; add 180 because zone 1 starts at 180 W.
    utm_zone = int((lon + 180) // 6 + 1)

    # 269 = northern hemisphere, 327 = southern hemisphere
    utm_proj = 'epsg:269{:02d}'.format(utm_zone) if lat > 0 else 'epsg:327{:02d}'.format(utm_zone)
    return utm_proj


def climb_angle(v: Iterable) -> np.ndarray:
    """
    Compute the 'climb angle' of a vector.
    A = 𝑛•𝑏=|𝑛||𝑏|𝑠𝑖𝑛(𝜃)

    Parameters
    ----------
    v : array-like
        Vector to compute the climb angle for.

    Returns
    -------
    degrees : ndarray of floats
        Corresponding climb angle value in degrees.
    """
    n = np.array([0, 0, 1])  # A unit normal vector perpendicular to the xy plane
    degrees = np.degrees(np.arcsin(np.dot(n, v) / np.linalg.norm(n) * np.linalg.norm(v)))
    return degrees


def interpolate_spline(points: 'Tracks', ds: int = 1) -> gpd.GeoDataFrame:
    """
    Interpolate points with a cubic spline between flight points, if possible.
    See https://docs.scipy.org/doc/scipy/reference/tutorial/interpolate.html#spline-interpolation for docs

    Parameters
    ----------
    points : Tracks
        A Track gpd.GeoDataframe object containing known track points in a path. A minimum of 2 points is required.
    ds : int, default 1
        The second interval in which to calculate the spline for.
        E.g. ds = 1 is "calculate a spline point at every 1 second delta"

    Returns
    -------
    gpd.GeoDataFrame of all points in the interpolated spline.
    Columns: point_dt, geometry

    Raises
    ------
    AssertionError if there is fewer than 1 Track point.
    """
    # Calculate the order of polynomial to fit to the spline. The maximum is a cubic spline. If there are fewer than
    #  3 points, a cubic spline cannot be fit and lower order must be chosen.
    assert points.shape[0] > 1, "A minimum of 2 points is required for calculate a spline."
    k = min(points.shape[0] - 1, 3)

    points.sort_values(by='point_dt', ascending=True, inplace=True)
    starttime = points.point_dt.iat[0]
    endtime = points.point_dt.iat[-1]
    flight_times = (points.point_dt - starttime).dt.total_seconds().values  # Seconds after initial point

    coords = [points.geometry.x, points.geometry.y, points.z] if 'z' in points else [points.geometry.x, points.geometry.y]
    tck, u = interpolate.splprep(x=coords, u=flight_times, k=k)

    # Parametric interpolation on the time interval provided.
    duration = (endtime - starttime).total_seconds()
    tnew = np.arange(0, duration + ds, ds)
    spl_out = interpolate.splev(tnew, tck)
    track_spline = gpd.GeoDataFrame({'point_dt': [starttime + dt.timedelta(seconds=offset) for offset in tnew]},
                                    geometry=[Point(xyz) for xyz in zip(spl_out[0], spl_out[1], spl_out[2])],
                                    crs=points.crs)
    return track_spline


def audible_time_delay(points: gpd.GeoDataFrame, time_col: str, target: Point,
                       m1: float = 343., drop_cols: bool = False) -> gpd.GeoDataFrame:
    """
    Given a set of points and a target location, calculate when a sound made at each point could be heard at
    the target.

    **IMPORTANT**: The points GeoDataFrame and the target Point should be in the same crs for accurate calculations.

    Parameters
    ----------
    points : gpd.GeoDataFrame
        A gpd.GeoDataFrame of sound location points.
    time_col : str
        Name of the column in the points gpd.GeoDataFrame with time of sound occurrence at each point.
    target : Point
        The target point.
    m1 : float, default 343 m/s
        The speed of sound to use for calculations. Make sure this value uses the same units as the crs of
        the points GeoDataFrame and the target Point.
    drop_cols : bool, default False
        If True, drop the intermediate columns used to determine time of audibility.

    Returns
    -------
    The points GeoDataFrame with added columns:
    Standard: time_audible
    Optional: distance_to_target, audible_delay_sec
    """
    points['distance_to_target'] = points.geometry.apply(lambda geom: target.distance(geom))
    points['audible_delay_sec'] = points['distance_to_target'] / m1
    points['time_audible'] = points.apply(lambda row: row[time_col] + dt.timedelta(seconds=row.audible_delay_sec), axis=1)

    if drop_cols:
        points.drop(['distance_to_target', 'audible_delay_sec'], inplace=True)

    return points


def build_src_point_mesh(area: gpd.GeoDataFrame, density: int = 48, altitude: Optional[int] = None) -> List[Point]:
    """
    Given a polygon and a density, create a square mesh of evenly spaced points throughout the polygon.

    Parameters
    ----------
    area : gpd.GeoDataFrame
        A GeoDataFrame of the area to create the square point mesh over.
    density : int
        The number of points along each mesh axis. The mesh will contain density x density points.
    altitude : int, default None
        A standard altitude to apply to every point in the mesh.

    Returns
    -------
    mesh points : List[Point]
        A list of shapely Points in the mesh.
    """
    # Start out with a grid of N = density x density points. Polygon bounds:  (minx, miny, maxx, maxy)
    x = np.linspace(area.total_bounds[0], area.total_bounds[2], density)
    y = np.linspace(area.total_bounds[1], area.total_bounds[3], density)
    x_ind, y_ind = np.meshgrid(x, y)

    # Create an array of mesh points. np.ravel linearly indexes an array into a row.
    mesh_points = np.array([np.ravel(x_ind), np.ravel(y_ind)]).T

    # Convert coordinate tuples into shapely points.
    mesh_points = [Point(point[0], point[1]) if not altitude
                   else Point(point[0], point[1], altitude) for point in mesh_points]

    return mesh_points


def create_overlapping_mesh(area: gpd.GeoDataFrame, spacing: int = 1,
                            mesh_size: int = 25) -> Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Create a mesh of polygons as close to size mesh_size x mesh_size as possible over a specific area.

    Parameters
    ----------
    area : gpd.GeoDataFrame
        The area to cover with the mesh. CRS should be a geographic coordinate system that uses D.d.
    spacing : int, default 1 km
        Distance apart receiver points should be in kilometers
    mesh_size : int, default 25 km
        The target size in kilometers of a mesh square (mesh_size x mesh_size)

    Returns
    -------
    An overlapping mesh of squares that cover the requested area.
    A GeoDataFrame of the center points used to create the mesh squares.
    """
    equal_area_crs = coords_to_utm(area.centroid.iat[0].y, area.centroid.iat[0].x)
    area_m = area.to_crs(equal_area_crs)

    minx, miny, maxx, maxy = area_m.total_bounds
    x = np.linspace(minx, maxx, math.ceil((maxx-minx)/(spacing*1000)))
    y = np.linspace(miny, maxy, math.ceil((maxy-miny)/(spacing*1000)))
    x_ind, y_ind = np.meshgrid(x, y)

    # np.ravel linearly indexes an array into a row.
    mesh_points = [Point(point[0], point[1]) for point in np.array([np.ravel(x_ind), np.ravel(y_ind)]).T]
    mesh_points = gpd.GeoDataFrame({'geometry': mesh_points}, geometry='geometry', crs=equal_area_crs)

    # Only keep points that fall within the study area.
    mesh_points = gpd.sjoin(mesh_points, area_m, op='within')[['geometry']]

    # Create mesh around points.
    mesh = mesh_points.buffer(mesh_size*1000, cap_style=3)

    mesh.reset_index(drop=True, inplace=True)
    mesh_points.reset_index(drop=True, inplace=True)

    return mesh.to_crs(area.crs), mesh_points.to_crs(area.crs)


def project_raster(input_raster: str, output_raster: str, crs: str):
    """
    Project a raster to a new crs

    Parameters
    ----------
    input_raster : str
        Absolute path to the raster to project.
    output_raster : str
        Absolute path to where the projected raster should be written.
    crs : crs
        The CRS to project the input raster to. Of the format: 'epsg:XXXX...'
    """
    gdal.Warp(output_raster, input_raster, dstSRS=crs)


def ambience_from_raster(ambience_src: str, mic: 'Microphone') -> float:
    """
    Select the ambience level from a broadband raster at a specific microphone location.

    Parameters
    ----------
    ambience_src : str
        The absolute file path to a raster of broadband ambience.
    mic : Microphone
        A Microphone object whose location to select the broadband ambience for.

    Returns
    -------
    Lx : float
        The ambience level at the microphone location.
    """
    with rasterio.open(ambience_src) as raster:
        projected_mic = mic.to_crs(raster.crs)
        band1 = raster.read(1)
        Lx = band1[raster.index(projected_mic.x, projected_mic.y)]

    return Lx


def ambience_from_nvspl(ambience_src: 'Nvspl', quantile: int = 50, broadband: bool = False): # TODO
    """

    Parameters
    ----------
    ambience_src : Nvspl
        An NVSPL object to calculate ambience from.
    quantile : int, default 50
        This quantile of the data will be used to calculate the ambience.
    broadband : bool, default False
        If True, quantiles will be calculated from the dBA column instead of the 1/3rd octave band columns.

    Returns
    -------
    Lx
    """
    if broadband:
        Lx = ambience_src.loc[:, 'dbA'].quantile(1 - (quantile / 100))
    else:
        Lx = ambience_src.loc[:, "12.5":"20000"].quantile(1 - (quantile / 100))

    return Lx


def compute_fbeta(valid_points: gpd.GeoDataFrame, active_space: gpd.GeoDataFrame,
                  beta: float = 1.0) -> Tuple[float, float, float, int]:
    """
    Given a set of annotated points and an active space geometry, compute accuracy metrics such as F1 score, precision,
    and recall.
        TP = True Positives
        FP = False Positives
        FN = False Negatives

    Parameters
    ------
    valid_points : gpd.GeoDataFrame
        Annotated points. Must include geometry and an 'audible' column.
    active_space : gpd.GeoDataFrame
        Polygon or Multipolygon of a computed active space.
    beta : float, default 1.0
        Beta value to use when calculating fbeta

    Returns
    -------
    fbeta : float
        fbeta score (more here: https://en.wikipedia.org/wiki/F-score)
    precision : float
        Defined TP/(TP+FP), measure of how well a positive test corresponds with an actual audible flight
    recall : float
        Defined TP/(TP+FN), measure of how well an audible flight is marked as audible by the given active space
    n_tot: int
        number of points annotated.
    """
    # Before computing anything, make sure projections match:
    if valid_points.crs != active_space.crs:
        valid_points.to_crs(active_space.crs, inplace=True)

    # iterate through all valid points and check if they are in the active space... this takes a while.
    in_AS_gdf = gpd.clip(valid_points, active_space)

    # make an `in_activespace` column and set to true for points inside mask
    valid_points['in_AS'] = False
    valid_points.loc[in_AS_gdf.index, 'in_AS'] = True

    in_AS = valid_points.in_AS.values  # convert both of these columns to boolean arrays for easier
    audible = valid_points.audible.values

    # compute true positives, etc.
    TP = np.all([in_AS, audible], axis=0).sum()
    FP = np.all([in_AS, ~audible], axis=0).sum()
    FN = np.all([~in_AS, audible], axis=0).sum()
    n_tot = len(valid_points)

    precision = TP / (TP + FP)  # specificity... if a flight enters the active space, is it actually audible?
    recall = TP / (TP + FN)  # sensitivity... if a flight is audible, does it enter the active space?
    fbeta = (1 + np.power(beta, 2)) * ((precision * recall) / ((np.power(beta, 2) * precision) + recall))

    return fbeta, precision, recall, n_tot

def contiguous_regions(condition):

    """
    Finds contiguous True regions of an input boolean array. 
    
    Parameters
    ----------
    condition : `np.ndarray` of dtype "bool" 
                 or `np.ndarray` and conditional logic statement to produce the boolean array 
                 e.g., arr    or    arr > 5.5

    Returns
    -------
    idx : 2-D numpy.ndarray
        A 2-D int array where the first column is the start index of each contiguous True region, 
        and the second column is the end index of each contiguous True region.

    """

    # Find the indicies of changes in "condition"
    d = np.diff(condition)
    idx, = d.nonzero() 

    # We need to start things after the change in "condition". Therefore, 
    # we'll shift the index by 1 to the right.
    idx += 1

    if condition[0]:
        # If the start of condition is True prepend a 0
        idx = np.r_[0, idx]

    if condition[-1]:
        # If the end of condition is True, append the length of the array
        idx = np.r_[idx, condition.size] # Edit

    # Reshape the result into two columns
    idx.shape = (-1,2)

    return idx

def audibility_to_interval(aud, invert=False):

    '''
    Given an audibility time series in 1-D binary format (e.g., detection/non-detection sequence)
    separate it into two, 2-D arrays of {begin, end} interval pairs. The first represents the temporal 
    bounds of each audible noise event, the second, each noise-free interval.
    
    Noise intervals are closed via observation, but to close noise-free intervals 
    we must account for the beginning and end of the observation record. Use of closed intervals 
    ensures that no index is considered to be both noise and not-noise.

    Parameters
    ----------
    aud : 1-D array-like
        A 1-D boolean array representing audibility states of detection (e.g., True or 1) and non-detection (False or 0).
    invert : bool
        Detection and non-detection are mapped to 0 and 1, respectively. If True, invert the mapping. Default: False.

    Returns
    -------
    noise_intervals: 2-D numpy.ndarray
        A 2-D int array of closed intervals bounding audible noise events.  
        The first value in the pair is the start index, the second value is the end index.
    noise_free_intervals: 2-D numpy.ndarray
        A 2-D int array bounding closed noise-free intervals.  
        The first value in the pair is the start index, the second value is the end index.
    '''
    aud = aud.astype('bool')
    if invert == True:
        aud = np.invert(aud) # invert detection mappings
    
    # compute naiive intervals
    noise_intervals = contiguous_regions(aud == True)
    noise_free_intervals_naiive = contiguous_regions(aud == False)
    
    nfi_starts = noise_free_intervals_naiive.T[0]
    nfi_ends = noise_free_intervals_naiive.T[1]

    # the record begins with noise...
    if(noise_intervals[0, 0] == 0):
        # ...the first noise free interval (and thus ALL intervals) 
        #    need to start one second later
        nfi_starts = nfi_starts + 1
    
    # the record begins with quietude...
    else:
        # ...the first noise free interval stays the same, and equals zero
        # the rest are + 1
        nfi_starts = nfi_starts + 1
        nfi_starts[0] = 0

    # the record ends with noise...
    if(noise_intervals[-1, 0] == 0):
        # ...the last noise free interval (and thus ALL intervals) need to end one second earlier
        nfi_ends = nfi_ends - 1
    
    # the record ends with quietude...
    else:
        # ...the last noise free interval stays the same, and equals zero
        # the rest are - 1
        save = nfi_ends[-1]
        print(save)
        nfi_ends = nfi_ends - 1
        nfi_ends[-1] = save

    # recompose NFIs using updated, correct values
    noise_free_intervals = np.array([nfi_starts, nfi_ends]).T
    
    return noise_intervals, noise_free_intervals

def calculate_duration_summary(noise_intervals):

    '''
    Compute durations from interval-based noise event data. 
    Summarize the central tendency and variability using parametric and non-parametric estimators.

    Parameters
    ----------
    noise_intervals: 2-D numpy.ndarray
        A 2-D int array where the first column is the start index of each contiguous True region, 
        and the second column is the end index of each contiguous True region.

    Returns
    -------
    duration_summary : tuple
        A tuple of duration-based acoustic metrics:
            idx [0] a list of each event's duration
            idx [1] the mean duration
            idx [2] the standard deviation of the durations
            idx [3] the median duration
            idx [4] the median absolute deviation of the durations
    '''

    # the durations, themselves, are found by differencing (end - begin)
    duration_list = noise_intervals.T[1] - noise_intervals.T[0]

    # mean duration
    mean = np.mean(duration_list)

    # standard deviation duration
    stdev = np.std(duration_list)

    # median duration
    median = np.percentile(duration_list, 50)

    # median absolute deviation of duration
    mad = np.percentile(np.absolute(duration_list - median), 50)

    # combine the results into a single array
    duration_summary = (duration_list, mean, stdev, median, mad)

    return duration_summary