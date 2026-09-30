# Dwarf Mini Multi-Night Siril Script

This script helps Siril process Dwarf Mini FITS files from more than one night.
It is meant for the Dwarf Mini folder layout and can work from files copied off
the device or from a backup.

## What it does

- Finds all usable light frames under `lights/`, even in subfolders.
- Groups frames by exposure, gain, temperature, and filter.
- Reuses matching master darks and the one flat for each filter.
- Calibrates the lights, keeps the CFA data intact, then Bayer-drizzles and stacks them.
- Combines matching groups from different nights when they belong together.
- Makes a separate result for each filter and exposure length.
- Optionally aligns those final results and blends multiple exposure lengths for the same filter.

Typical output files look like this:

    result_astro_30s.fit
    result_duoband_30s.fit
    result_duoband_60s.fit

If a filter has multiple exposure lengths and `align_filters = true`, the
script also creates a combined result such as:

    result_astro.fit
    result_duoband.fit

Only filters that are actually present in the data are written.

## Requirements

- Siril 1.4.0 or newer
- Dwarf Mini FITS light frames
- Matching master darks and master flats

## Folder Setup

Put your lights inside a `lights/` folder in the Siril working directory:

    Siril working directory/
    └── lights/
        ├── light1.fits
        ├── light2.fits
        └── ...

The script searches subfolders too, so you do not need to sort files by night.
Filenames must include the exposure, gain, filter name, capture date, and
temperature in the format the script expects.

Master calibration frames are read from the Dwarf folder defined in the script:

    CALIBRATION_DIR = r"Z:\Astronomy\CALI_FRAME"
    CALIBRATION_CAMERA = "cam_0"

## Configuration

You can add an optional `config.ini` in the Siril working directory. Missing
values fall back to the defaults below:

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

Short version of the main settings:

- `drizzle_scale` sets the Bayer-drizzle output size.
- `pixel_fraction` sets the drizzle drop size.
- `use_weighted_fwhm` controls stack weighting: `true` uses weighted-FWHM, `false` uses noise weighting.
- `filter_*` keeps only the best percentage of registered frames by FWHM,
  roundness, background, and star count. Set a value to `100.0` to disable it.
- `sigma_low` and `sigma_high` control winsorized sigma rejection.
- `align_filters` lines up the final results and also allows exposure-length blending.
- `keep_intermediates` keeps the temporary processing files instead of deleting them.
- `result_name` sets the output file prefix.

If `align_filters = true` and a filter has more than one exposure length, the
script blends the aligned masters using noise-based weights.

## Notes

- Darks must match exposure and gain exactly.
- Temperature does not need to match exactly; the closest dark is used.
- If the temperature difference is more than 5°C, the script warns you.
- Each filter needs exactly one flat: `ir_1` for Astro and `ir_2` for Duo-Band.
- The `process/` folder is recreated every time. Do not keep anything there.
- The script loads the final result in Siril when it finishes.
