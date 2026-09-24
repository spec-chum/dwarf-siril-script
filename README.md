# Dwarf Mini Multi-Night Siril Script

A Python script for [Siril](https://siril.org/) that processes Dwarf Mini FITS data across multiple nights, exposure times, gains, temperatures and filters.

It is designed to work directly from the Dwarf Mini's file structure, either from the original files on the Dwarf or from a backed-up copy.

## What it does

The script automatically:

- Finds and groups the Dwarf's light frames by exposure, gain, temperature and filter.
- Groups matching frames together regardless of which night they were captured.
- Selects the matching master dark for each exposure and gain, using the closest available temperature.
- Applies the single matching `ir_1` or `ir_2` master flat for each filter.
- Calibrates with dark subtraction, flat correction, CFA equalization and
  3-sigma dark-derived cosmetic correction.
- Keeps calibrated lights as undebayered CFA data until Bayer drizzle.
- Merges calibrated groups that have the same filter and exposure length.
- Registers with global homography alignment and Bayer-drizzles using the
  configured scale and pixel fraction.
- Stacks with winsorized sigma rejection, additive-with-scaling normalization,
  optional weighted-FWHM weighting, and optional frame-quality filters.
- Aligns the completed filter/exposure stacks to common framing.
- When a filter has multiple exposure lengths, linear-matches them to the
  shortest-exposure master and combines them while still linear using measured
  inverse-variance (background-noise) weights.

Different exposure lengths are stacked separately. Different gains are
calibrated separately with their matching darks, but calibrated frames with the
same filter and exposure length are subsequently merged into one stack.

Every filter/exposure stack is retained in the Siril working directory, for
example:

    result_astro_30s.fit
    result_duoband_30s.fit
    result_duoband_60s.fit

If a filter has two or more exposure lengths and `align_filters = true`, an
additional blended result is produced:

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

Put all usable `.fit` or `.fits` light frames from every night in this tree;
subdirectories are searched recursively, so the files do not need to be sorted
by night. Filenames must contain the Dwarf exposure, gain, `Astro` or
`Duo-Band` filter name, capture date, and temperature in the format expected by
the script. Frames that should not contribute should be removed beforehand or
excluded with the quality filters below.

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

`drizzle_scale` controls the Bayer-drizzle output scale. `pixel_fraction`
controls the drizzle drop size; the script uses Siril's square drizzle kernel.

When `use_weighted_fwhm = true`, Siril weights accepted frames by weighted
FWHM during stacking.

The four `filter_*` settings retain the specified percentage of best registered
frames for weighted FWHM, roundness, background and star count. A value of
`100.0` disables that filter, which means the supplied defaults do not reject
frames by these quality measurements.

`sigma_low` and `sigma_high` are the lower and upper thresholds for winsorized
sigma rejection.

`align_filters` controls the common alignment of all completed
filter/exposure masters. It must also be enabled for multiple exposure lengths
to be blended into a filter-level result.

Final images are written to the Siril working directory using `result_name`.
On completion, the script loads a blended filter result when one exists;
otherwise it loads the first filter/exposure result.

Exposure-master blending measures noise after alignment and linear matching,
then assigns each master a weight proportional to `1 / noise²`. Because this is
the noise of the completed master, it already reflects its accepted frames,
within-stack wFWHM weights and total integration time; `LIVETIME` is reported
but is not multiplied into the weight a second time. Every master receives a
positive, image-wide contribution. The blend is not an HDR or fixed-opacity
operation and does not stretch either input.

## Notes

Dark matching is strict for **exposure and gain**. Temperature does not need to
match exactly; the closest available master dark is selected, with the
larger-stack master preferred when temperature differences tie. A warning is
issued if the temperature difference exceeds 5°C.

Exactly one master flat matching each present filter (`ir_1` for Astro or
`ir_2` for Duo-Band) must be available. Calibration masters are copied into the
temporary processing tree and are never modified.

The `process/` folder is deleted and recreated at the start of each run. **Do not store anything in this folder that you want to keep.**

Intermediate processing files can be retained with `keep_intermediates = true` for troubleshooting or inspection.

When more than one filter/exposure result is produced, `align_filters = true`
registers all of them together with two-pass global registration and crops them
to their common area. Set it to `false` to leave the results independently
framed and skip exposure-length blending.
