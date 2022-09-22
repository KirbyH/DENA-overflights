import datetime as dt
import datetime as dt
import glob
import re
import os
from dataclasses import dataclass, field
from typing import List, Optional, Union

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from tqdm import tqdm


__all__ = [
    'Adsb',
    'Annotations',
    'EarlyAdsb',
    'Microphone',
    'Nvspl',
    'Tracks'
]


@dataclass
class Microphone:
    """
    An object representing a microphone deployment location.

    Parameters
    ----------
    name : str
        A name for the Microphone instance.
    lat : float
        The latitude of the microphone deployment location in WGS84 (epsg:4326)
    lon : float
        The longitude of the microphone deployment location in WGS84 (epsg:4326)
    z : float
        The elevation of the microphone deployment location in meters.
    crs : str, default None
        Epsg projected coordinated system to calculate the x, y values in. E.g. 'epsg:4326'
        Latitude and Longitude will not be projected if no crs is provided.

    Instance Variables
    ------------------
    x : float
        The longitude value projected into the current crs.
    y : float
        The latitude value projected into the current crs.
    """
    name: str
    lat: float
    lon: float
    z: float
    crs: str = None
    x: float = field(init=False)
    y: float = field(init=False)

    def __repr__(self):
        return f"Microphone(name={self.name})"

    def __post_init__(self):
        """Set x,y coordinates and instance name."""
        if self.crs:
            self.to_crs(self.crs)

    def to_crs(self, crs: str, inplace: bool = False) -> Optional['Microphone']:
        """
        Project instance x,y values to a new coordinate system.

        Parameters
        ----------
        crs : str
            The coordinate system to project the instance to.
                Format: epsg:XXXX. E.g. epsg:26906
        inplace : bool, default False
            If True, crs will be updated and no instance will be returned.
            If False, crs will be updated an the updated instance will be returned.
        """
        projection = Transformer.from_crs('epsg:4326', crs, always_xy=True)
        self.x, self.y = projection.transform(self.lon, self.lat)
        self.crs = crs
        if not inplace:
            return self


class Nvspl(pd.DataFrame):
    """
    A pandas DataFrame wrapper class to ensure consistent NVSPL data.

    Parameters
    ----------
    filepaths_or_data : List, str, or pd.DataFrame
        A directory containing NVSPL files, a list of NVSPL files, or an existing pd.DataFrame of NVSPL data.
    """

    standard_fields = {
        'SiteID', 'STime', 'dbA', 'dbC', 'dbF',
        'Voltage', 'WindSpeed', 'WindDir', 'TempIns',
        'TempOut', 'Humidity', 'INVID', 'INSID',
        'GChar1', 'GChar2', 'GChar3', 'AdjustmentsApplied',
        'CalibrationAdjustment', 'GPSTimeAdjustment',
        'GainAdjustment', 'Status'
    }

    octave_regex = re.compile(r"^H[0-9]+$|^H[0-9]+p[0-9]$")

    def __init__(self, filepaths_or_data: Union[List[str], str, pd.DataFrame]):
        data = self._read(filepaths_or_data)
        data['STime'] = data['STime'].astype('datetime64[s]')
        data.set_index('STime', inplace=True)
        super().__init__(data=data)

    def _read(self, filepaths_or_data: Union[List[str], str, pd.DataFrame]):
        """
        Read in and validate the NVSPL data.

        # TODO: for speed and memory improvements, use usecols, define datatypes, and drop empty columns.

        Parameters
        ----------
        filepaths_or_data : List, str, or pd.DataFrame
            A directory containing NVSPL files, a list of NVSPL files, or an existing pd.DataFrame of NVSPL data.

        Raises
        ------
        AssertionError if directory path or file path does not exists or is of the wrong format.
        """
        if isinstance(filepaths_or_data, pd.DataFrame):
            self._validate(filepaths_or_data.columns)
            data = filepaths_or_data

        else:
            if isinstance(filepaths_or_data, str):
                assert os.path.isdir(filepaths_or_data), f"{filepaths_or_data} does not exist."
                filepaths_or_data = glob.glob(f"{filepaths_or_data}/*.txt")

            else:
                for file in filepaths_or_data:
                    assert os.path.isfile(file), f"{file} does not exist."
                    assert file.endswith('.txt'), f"Only .txt NVSPL files accepted."

            data = pd.DataFrame()
            for file in tqdm(filepaths_or_data, desc='Loading NVSPL files', unit='files', colour='green'):
                df = pd.read_csv(file)
                self._validate(df.columns)
                data = data.append(df)

        octave_columns = {c: c.replace('H', '').replace('p', '.') for c in filter(self.octave_regex.match, data.columns)}
        data.rename(columns=octave_columns, inplace=True)

        return data

    def _validate(self, columns: List[str]):
        """
        Ensure that the provided data has only the standard

        Parameters
        ----------
        columns : List of strs
            List of NVSPL DataFrame columns.

        Raises
        ------
        AssertionError if any standard column is missing or if any non-standard and non-octave column is present.
        """
        # Verify that all NVSPL standard columns exist.
        missing_standard_cols = self.standard_fields - set(columns)
        assert missing_standard_cols == set(), f"Missing the following standard NVSPL columns: {missing_standard_cols}"

        # Verify all non-standard columns are octave columns.
        only_standard_cols = all(re.match(self.octave_regex, col) for col in (set(columns) - self.standard_fields))
        assert only_standard_cols is True, "NVSPL data contains unexpected NVSPL columns."


class Adsb(gpd.GeoDataFrame):
    """
    A geopandas GeoDataFrame wrapper class to ensure consistent ADS-B data.

    Parameters
    ----------
    filepaths_or_data : List, str, or gpd.GeoDataFrame
        A directory containing ADS-B TSV files, a list of ADS-B TSV files, or an existing gpd.GeoDataFrame of ADS-B data.
    """

    def __init__(self, filepaths_or_data: Union[List[str], str, gpd.GeoDataFrame]):
        data = self._read(filepaths_or_data)
        data.drop_duplicates(subset=['TIME'], inplace=True, keep = 'last')
        super().__init__(data=data)

    def _read(self, filepaths_or_data: Union[List[str], str, gpd.GeoDataFrame]):
        """
        Read in ADS-B points as formatted by NPS data loggers.

        Parameters
        ----------
        filepaths_or_data : List, str, or gpd.GeoDataFrame
            A directory containing ADS-B files, a list of ADS-B files, or an existing gpd.GeoDataFrame of ADS-B data.

        Raises
        ------
        AssertionError if directory path or file path does not exists or is of the wrong format.
        """
        if isinstance(filepaths_or_data, gpd.GeoDataFrame):
            data = filepaths_or_data.to_crs("epsg:4326")

        else:
            if isinstance(filepaths_or_data, str):
                assert os.path.isdir(filepaths_or_data), f"{filepaths_or_data} does not exist."
                filepaths_or_data = glob.glob(f"{filepaths_or_data}/*.TSV")

            else:
                for file in filepaths_or_data:
                    assert os.path.isfile(file), f"{file} does not exist."
                    assert (file.endswith('.txt')|file.endswith('.TSV')), f"Only .TSV ADS-B files accepted."

            data = pd.DataFrame()
            for file in tqdm(filepaths_or_data, desc='Loading ADS-B files', unit='files', colour='green'):
                df = pd.read_csv(file, sep="\t")

                mask = df.iloc[:, 0].isin(["TIME", "timestamp"])
                df = df[~mask]
                header_list = ["TIME", "timestamp"]
                import_header = df.axes[1]
                result = any(elem in import_header for elem in header_list)
                if result:
                    pass
                else:
                    raise KeyError

                # Standardize key field names and remove extra columns collected by the ADS-B df logger
                if "timestamp" in df.columns:
                    df = df.rename(columns={"timestamp":"TIME"})
                if "valid_flags" in df.columns:
                    df = df.rename(columns={"valid_flags":"validFlags"})
                df.drop(["squawk", "altitude_type", "alt_type", "altType", "callsign",
                    "emitter_type", "emitterType"], axis=1, inplace=True, errors="ignore")

                # Delete duplicate and NA records
                df.drop_duplicates(inplace=True)
                df.dropna(how="any", axis=0, inplace=True)

                # Unpack validFLags and convert the 2-byte flag field into a list of Boolean values
                flags_names = ["valid_BARO", "valid_VERTICAL_VELOCITY", "SIMULATED_REPORT", "valid_IDENT",
                            "valid_CALLSIGN", "valid_VELOCITY", "valid_HEADING", "valid_ALTITUDE", "valid_LATLON"]
                flags = df["validFlags"].apply(lambda t: list(bin(int(t, 16))[2:].zfill(9)[-9:]))
                flags_df = pd.DataFrame(list(flags), columns=flags_names).replace({'0': False, '1': True})
                df = pd.concat([df.drop("validFlags", axis=1), flags_df], axis=1)

                # Keep only those records with valid latlon and altitude values based on validFlags
                df.dropna(how="any", axis=0, inplace=True)
                if df["valid_LATLON"].sum() == len(df.index):
                    invalidLatLon = 0
                else:
                    invalidLatLon = round(100 - df["valid_LATLON"].sum() / len(df.index) * 100, 2)
                if df["valid_ALTITUDE"].sum() == len(df.index):
                    invalidAltitude = 0
                else:
                    invalidAltitude = round(100 - df["valid_ALTITUDE"].sum() / len(df.index) * 100, 2)
                df.drop(df[df["valid_LATLON"] == "False"].index, inplace = True)
                df.drop(df[df["valid_ALTITUDE"] == "False"].index, inplace = True)

                # Ensure remaining field values except TIME are in proper numeric format
                df.replace('-', np.NaN, inplace=True)
                df.dropna(how="any", axis=0, inplace=True)
                df["ICAO_address"] = df["ICAO_address"].astype(str)
                df["lat"] = df["lat"].astype(int)
                df["lon"] = df["lon"].astype(int)
                df["altitude"] = df["altitude"].astype(int)
                df["heading"] = df["heading"].astype(int)
                df["hor_velocity"] = df["hor_velocity"].astype(int)
                df["ver_velocity"] = df["ver_velocity"].astype(int)
                df["tslc"] = df["tslc"].astype(int)

                # Convert Unix timestamp to datetime objects in UTC and re-scale selected variable values
                df["TIME"] = pd.to_datetime(df["TIME"], unit = "s")
                df["DATE"] = df["TIME"].dt.strftime("%Y%m%d")
                df["lat"] = df["lat"] / 1e7
                df["lon"] = df["lon"] / 1e7
                df["altitude"] = df["altitude"] / 1e3
                df["heading"] = df["heading"] / 1e2
                df["hor_velocity"] = df["hor_velocity"] / 1e2
                df["ver_velocity"] = df["ver_velocity"] / 1e2

                # Keep only those records with TSLC values of 1 or 2 seconds
                invalidTslc = len(df.query("tslc >= 3 or tslc == 0")) / df.shape[0] * 100
                df.drop(df[df["tslc"] >= 3].index, inplace = True)
                df.drop(df[df["tslc"] == 0].index, inplace = True)

                # Sort records by ICAO Address and TIME then reset dfframe index
                df.sort_values(["ICAO_address", "TIME"], inplace=True, ignore_index=True)

                # Calculate time difference between sequential waypoints for each aircraft
                df["dur_secs"] = df.groupby("ICAO_address")["TIME"].diff().dt.total_seconds()
                df["dur_secs"] = df["dur_secs"].fillna(0)

                # Count then delete any identical waypoints in a single input file based on ICAO_address, time, lat, and lon
                duplicateWaypoints = 100 - (len(df.drop_duplicates(subset=['ICAO_address', 'TIME', 'lat', 'lon'])) / len(df) * 100)
                df.drop_duplicates(subset=['ICAO_address', 'TIME', 'lat', 'lon'], keep = 'last')

                # Use threshold waypoint duration value to identify separate flights by an aircraft then sum the number of "true" conditions to assign unique ID's
                df['diff_flight'] = df['dur_secs'] >= 900
                df['cumsum'] = df.groupby('ICAO_address')['diff_flight'].cumsum()
                df['flight_id'] = df['ICAO_address'] + "_" + df['cumsum'].astype(str) + "_" + df['DATE']

                # Remove records where there is only one recorded waypoint for an aircraft and fields that are no longer needed
                df = df[df.groupby("flight_id").flight_id.transform(len) > 1]
                df = df.drop(columns = ['tslc', 'dur_secs', 'diff_flight', 'cumsum', 'valid_BARO', 'valid_VERTICAL_VELOCITY', 'SIMULATED_REPORT', 'valid_IDENT', 'valid_CALLSIGN', 'valid_VELOCITY', 'valid_HEADING', 'valid_ALTITUDE', 'valid_LATLON', 'DATE'])

                data = data.append(df)

            data = gpd.GeoDataFrame(
                data,
                geometry=gpd.points_from_xy(data["lon"], data["lat"]),
                crs="epsg:4326"
            )

        return data


class EarlyAdsb(Adsb):
    """
    A geopandas GeoDataFrame wrapper class to ensure consistent ADS-B data.

    Parameters
    ----------
    filepaths_or_data : List, str, or gpd.GeoDataFrame
        A directory containing ADS-B TSV files, a list of ADS-B TSV files, or an existing gpd.GeoDataFrame of ADS-B data.
    """

    def __init__(self, filepaths_or_data: Union[List[str], str, gpd.GeoDataFrame]):
        data = self._read(filepaths_or_data)
        data.drop_duplicates(subset=['TIME'], inplace=True, keep='last')
        super().__init__(data=data)

    def _read(self, filepaths_or_data: Union[List[str], str, gpd.GeoDataFrame]):
        """
        Read in ADS-B points as formatted by early-development NPS data loggers (circa 2019).

        Parameters
        ----------
        filepaths_or_data : List, str, or gpd.GeoDataFrame
            A directory containing ADS-B files, a list of ADS-B files, or an existing gpd.GeoDataFrame of ADS-B data.

        Raises
        ------
        AssertionError if directory path or file path does not exists or is of the wrong format.
        """
        if isinstance(filepaths_or_data, gpd.GeoDataFrame):
            data = filepaths_or_data.to_crs("epsg:4326")

        else:
            if isinstance(filepaths_or_data, str):
                assert os.path.isdir(filepaths_or_data), f"{filepaths_or_data} does not exist."
                filepaths_or_data = glob.glob(f"{filepaths_or_data}/*.TSV")

            else:
                for file in filepaths_or_data:
                    assert os.path.isfile(file), f"{file} does not exist."
                    assert (file.endswith('.txt')), f"Only .txt ADS-B files accepted."

            data = pd.DataFrame()
            for file in tqdm(filepaths_or_data, desc='Loading ADS-B files', unit='files', colour='green'):
                df = pd.read_csv(file, sep="\t")

                df.columns = ["ICAO_address", "TIME", "lat", "lon", "altitude"]
                df["TIME"] = df["TIME"].apply(lambda t: dt.datetime.strptime(t, "%Y/%m/%d %H:%M:%S.%f").replace(microsecond=0))
                df["DATE"] = df["TIME"].dt.strftime("%Y%m%d")

                # Sort records by ICAO Address and TIME then reset dataframe index
                df.sort_values(["ICAO_address", "TIME"], inplace=True, ignore_index=True)

                # Calculate time difference between sequential waypoints for each aircraft
                df["dur_secs"] = df.groupby("ICAO_address")["TIME"].diff().dt.total_seconds()
                df["dur_secs"] = df["dur_secs"].fillna(0)

                # Use threshold waypoint duration value to identify separate flights by an aircraft
                # then sum the number of "true" conditions to assign unique ID's
                df['diff_flight'] = df['dur_secs'] >= 900
                df['cumsum'] = df.groupby('ICAO_address')['diff_flight'].cumsum()
                df['flight_id'] = df['ICAO_address'] + "_" + df['cumsum'].astype(str) + "_" + df['DATE']

                # Remove records where there is only one recorded waypoint for an aircraft
                df = df[df.groupby("flight_id").flight_id.transform(len) > 1]

                data = data.append(df)

            data = gpd.GeoDataFrame(
                data,
                geometry=gpd.points_from_xy(data["lon"], data["lat"]),
                crs="epsg:4326"
            )

        return data


class Tracks(gpd.GeoDataFrame):
    """
    A geopandas GeoDataFrame wrapper class to standardize track points.

    Parameters
    ----------
    data : gpd.GeoDataFrame
        A GeoDataFrame of track points.
    id_col : str
        The name of the column containing aa unique identifier to group track points by.
        This column will be given the standardized name of track_id and converted to a string.
            E.g. flight id, license plate
    datetime_col : str
        A tracks GeoDataFrame is required to have a column with the datetime of each track point.
        This column will be given the standardized name of "point_dt".
    z_col : str, default None
        A tracks GeoDataFrame can have a column with the altitude of the points.
        This column will be given the standardized name of "z".

    Notes
    -----
    Currently, there is a bug with GeoPandas where running to_crs() will delete the z values of Points as mentioned
    in this post https://stackoverflow.com/questions/72987452/geopands-to-crs-dropping-z-values. Therefore, z values must
    be kept in a separate standard column until this bug has been resolved.
    """
    def __init__(self, data: gpd.GeoDataFrame, id_col: str, datetime_col: str, z_col: Optional[str] = None):
        col_renames = {id_col: 'track_id', datetime_col: 'point_dt'}
        if z_col:
            col_renames[z_col] = 'z'
        data.rename(columns=col_renames, inplace=True)
        if 'geometry' not in data:
            data.rename_geometry('geometry', inplace=True)
        data['track_id'] = data.track_id.astype(str)
        data.sort_values(by=['track_id', 'point_dt'], ascending=True, inplace=True)
        super().__init__(data=data)


class Annotations(gpd.GeoDataFrame):
    """
    A geopandas GeoDataFrame wrapper class to standardize track annotations.

    Parameters
    ----------
   filename : str, default None
       Filename to read annotation data from. If no filename is passed, an empty Annotations GeoDataFrame
       will be created.
    """
    def __init__(self, filename: Optional[str] = None):

        if filename:
            data = gpd.read_file(filename).astype({'start_dt': 'datetime64[ns]', 'end_dt': 'datetime64[ns]'})

            # Sometimes the annotation file is read in with the valid and audible columns as booleans and other times
            #  as objects depending on what values are stored.
            try:
                data.valid.replace({'1': True, '0': False}, inplace=True)
                data.audible.replace({'1': True, '0': False}, inplace=True)
            except TypeError:
                pass

        else:

            data = gpd.GeoDataFrame(columns=['_id', 'start_dt', 'end_dt', 'valid', 'audible', 'geometry', 'note'],
                                    geometry='geometry', crs='epsg:4326')

        super().__init__(data=data, crs=data.crs)
