# NPS Active Space

An ***active space*** is a well-known concept in bioacoustics ([Marten and Marler 1977](https://www.jstor.org/stable/pdf/4599136.pdf)). It represents a geographic volume whose radii correspond to the limit of audibility for a specific signal. In other words, an active space provides an answer to the question, *"how far can you hear a given sound at a specific place on Earth's surface?"*

This repository is designed to estimate active spaces for fixed-wing aircraft noise within the U.S. National Park System. Aircraft are powerful noise sources audible over vast areas. Thus [considerable NPS management efforts have focused on protecting natural quietude from aviation noise](https://www.nps.gov/subjects/sound/overflights.htm). `NPS-ActiveSpace` provides a quantitative tool to support managers in monitoring park resource condition. 

## Packages

This project is made up for four packages:

`utils`: diverse utilities - file I/O, geoprocessing computations, acoustic propagation modelling, and detection statistics
    
`ground-truthing`: a `tkinter`-based ground-truthing application

`active-space`: generate and tune active space polygons

`analysis`: estimate acoustic metrics from the intersection of an active space polygon and vehicle tracks

Also included are noise source [data](https://github.com/dbetchkal/NPS-ActiveSpace/tree/v2/nps_active_space/data) for tuning active space polygons.

## License

**Authors**: <br>Kirby Heck<br>Adina Zucker<br>Davyd Halyn Betchkal

### Public domain

This project is in the worldwide [public domain](LICENSE.md):

> This project is in the public domain within the United States,
> and copyright and related rights in the work worldwide are waived through the
> [CC0 1.0 Universal public domain dedication](https://creativecommons.org/publicdomain/zero/1.0/).
>
> All contributions to this project will be released under the CC0 dedication.
> By submitting a pull request, you are agreeing to comply with this waiver of copyright interest.
