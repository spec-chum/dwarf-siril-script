# Dwarf Mini Multi-Night Siril Script

A Python script for [Siril](https://siril.org/) that processes Dwarf Mini FITS data across multiple nights, exposure times, gains, temperatures and filters.

It is designed to work directly from the Dwarf Mini's file structure, either from the original files on the Dwarf or from a backed-up copy.

## What it does

The script automatically:

- Finds and groups the Dwarf's light frames by exposure, gain, temperature and filter.
- Groups matching frames together regardless of which night they were captured.
- Selects the matching master dark for each exposure and gain, using the closest available temperature.
- Applies the appropriate master flat for each filter.
- Calibrates and debayers single frames where necessary.
- Registers and Bayer-drizzles multi-frame groups independently.
- Stacks each group with sigma rejection.
- Registers and combines the resulting groups separately into Astro and Duo-Band final images.

Different exposure lengths and gains are **not mixed during the initial stacking**, allowing datasets containing, for example, both 30s and 60s exposures or different gains to be processed correctly.

The final outputs are:

    result_astro.fit
    result_duoband.fit

Only the filter types actually present in the data are produced.

## Requirements

- Siril 1.4.0+
- Dwarf Mini FITS data
- Matching master darks and master flats

## Setup

The script expects a lights/ folder inside the Siril working directory:

Siril working directory/
└── lights/
    ├── light1.fits
    ├── light2.fits
    └── ...

Put all the `.fits` files in there from every night you want to process, even the failed files if you want. You don't need to sort them.

Master calibration frames are read directly from the Dwarf's filesystem, either from a local copy or the device itself:

    CALIBRATION_DIR = r"Z:\Astronomy\CALI_FRAME"
    CALIBRATION_CAMERA = "cam_0"

## Configuration

Processing options can be set in an optional `config.ini` in the Siril working directory. The following are the default values:

    [processing]
    drizzle_scale = 1.0
    pixel_fraction = 1.0

    use_weighted_fwhm = true

    filter_wfwhm = 100.0
    filter_round = 100.0
    filter_background = 100.0
    filter_star_count = 100.0
    sigma_low = 3.0
    sigma_high = 3.0

    align_filters = true

    keep_intermediates = false
    result_name = result

If `config.ini` is not present, or any of the values are missing, the script uses its built-in defaults.

The final images are written to the Siril working directory using `result_name` and are loaded automatically when processing finishes.

## Notes

Dark matching is strict for **exposure and gain**. Temperature does not need to match exactly; the closest available master dark is selected. A warning is issued if the temperature difference exceeds 5°C.

The `process/` folder is deleted and recreated at the start of each run. **Do not store anything in this folder that you want to keep.**

Intermediate processing files can be retained with `keep_intermediates = true` for troubleshooting or inspection.

When both Astro and Duo-Band results are produced, `align_filters = true` registers them against each other and uses common framing so the two final images are pixel-aligned. Set it to `false` to leave the two results independently framed.
