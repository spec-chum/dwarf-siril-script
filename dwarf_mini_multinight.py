import configparser
import os
import re
import shutil
from collections import defaultdict
from datetime import datetime

import sirilpy as s


# ---------------------------- settings -------------------------------------

DRIZZLE_KERNEL = "square"


# Percentage of best frames to keep. 100 disables a filter.
FILTER_FWHM = 100
FILTER_ROUND = 100
FILTER_BACKGROUND = 100
FILTER_STAR_COUNT = 100

USE_WEIGHTED_FWHM = True
DARK_TEMP_WARNING_C = 5.0


# Lights remain in the Siril working directory; darks/flats are read from here.
CALIBRATION_DIR = r"Z:\Astronomy\CALI_FRAME"
CALIBRATION_CAMERA = "cam_0"


# ---------------------------- parsing -------------------------------------

LIGHT_RE = re.compile(
    r"_(?P<exp>[0-9]+(?:\.[0-9]+)?)s(?P<gain>[0-9]+(?:\.[0-9]+)?)_"
)

LIGHT_FILTER_RE = re.compile(
    r"_(?P<filter>Astro|Duo-Band)_",
    re.IGNORECASE,
)

LIGHT_DATE_TEMP_RE = re.compile(
    r"_(?P<date>[0-9]{8})-[0-9]+_(?P<temp>-?[0-9]+(?:\.[0-9]+)?)C"
    r"(?:\.fits?)?$",
    re.IGNORECASE,
)

DARK_RE = re.compile(
    r"dark_exp_(?P<exp>[0-9]+(?:\.[0-9]+)?)_"
    r"gain_(?P<gain>[0-9]+(?:\.[0-9]+)?)_"
    r".*?_(?P<temp>-?[0-9]+(?:\.[0-9]+)?)C"
    r"(?:_stack_[0-9]+)?(?:\([^)]*\))?\.fits?$",
    re.IGNORECASE,
)


def parse_light(path):
    name = os.path.basename(path)
    eg = LIGHT_RE.search(name)
    dt = LIGHT_DATE_TEMP_RE.search(name)
    filter_match = LIGHT_FILTER_RE.search(name)

    if not eg or not dt or not filter_match:
        raise ValueError(f"Cannot parse Mini light filename: {name}")

    filter_name = filter_match.group("filter").lower()
    filter_id = "1" if filter_name == "astro" else "2"

    return {
        "path": path,
        "date": datetime.strptime(dt.group("date"), "%Y%m%d").date(),
        "exposure": float(eg.group("exp")),
        "gain": float(eg.group("gain")),
        "temperature": float(dt.group("temp")),
        "filter": filter_id,
    }

def parse_dark(path):
    name = os.path.basename(path)
    match = DARK_RE.search(name)

    if not match:
        raise ValueError(f"Cannot parse Mini dark filename: {name}")

    stack_match = re.search(r"_stack_(?P<count>[0-9]+)", name, re.IGNORECASE)
    stack_count = int(stack_match.group("count")) if stack_match else 1

    return {
        "path": path,
        "exposure": float(match.group("exp")),
        "gain": float(match.group("gain")),
        "temperature": float(match.group("temp")),
        "stack_count": stack_count,
    }


def choose_dark(darks, exposure, gain, temperature):
    candidates = [
        dark
        for dark in darks
        if abs(dark["exposure"] - exposure) < 0.001
        and abs(dark["gain"] - gain) < 0.001
        and abs(dark["temperature"] - temperature) < 0.001
    ]

    if not candidates:
        return None

    return max(candidates, key=lambda dark: dark["stack_count"])


# ---------------------------- helpers -------------------------------------

def fits_files(folder):
    result = []
    for root, _, names in os.walk(folder):
        for name in names:
            if name.lower().endswith((".fit", ".fits")):
                result.append(os.path.join(root, name))
    return sorted(result)


def run(siril, *args):
    siril.log("> " + " ".join(str(x) for x in args), s.LogColor.BLUE)
    siril.cmd(*[str(x) for x in args])


def remove(path):
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.remove(path)


def copy_as_sequence(files, destination):
    os.makedirs(destination, exist_ok=True)
    for index, source in enumerate(sorted(files), 1):
        # Siril's configured FITS sequence extension is .fit.
        # Dwarf files may arrive as either .fit or .fits, so always
        # copy them into the working sequence using .fit.
        target = os.path.join(destination, f"light_{index:06d}.fit")
        shutil.copy2(source, target)


def filter_args(wfwhm_percent=100.0):
    args = []

    if FILTER_FWHM < 100:
        args.append(f"-filter-fwhm={FILTER_FWHM}%")
    if wfwhm_percent < 100:
        args.append(f"-filter-wfwhm={wfwhm_percent}%")
    if FILTER_ROUND < 100:
        args.append(f"-filter-round={FILTER_ROUND}%")
    if FILTER_BACKGROUND < 100:
        args.append(f"-filter-bkg={FILTER_BACKGROUND}%")
    if FILTER_STAR_COUNT < 100:
        args.append(f"-filter-nbstars={FILTER_STAR_COUNT}%")

    return args


# ---------------------------- main ----------------------------------------

def main():
    siril = s.SirilInterface()
    siril.connect()

    try:
        run(siril, "requires", "1.4.0")

        root = os.path.abspath(siril.get_siril_wd())

        config_file = os.path.join(root, "config.ini")
        config = configparser.ConfigParser()

        config_exists = os.path.isfile(config_file)
        if config_exists:
            config.read(config_file, encoding="utf-8")

        drizzle_scale = config.getfloat(
            "processing", "drizzle_scale", fallback=1.0
        )
        pixel_fraction = config.getfloat(
            "processing", "pixel_fraction", fallback=1.0
        )
        wfwhm_percent = config.getfloat(
            "processing", "wfwhm_percent", fallback=100.0
        )
        sigma_low = config.getfloat(
            "processing", "sigma_low", fallback=3.0
        )
        sigma_high = config.getfloat(
            "processing", "sigma_high", fallback=3.0
        )
        keep_intermediates = config.getboolean(
            "processing", "keep_intermediates", fallback=False
        )
        result_name = config.get(
            "processing", "result_name", fallback="result"
        )


        lights_dir = os.path.join(root, "lights")
        darks_dir = os.path.join(CALIBRATION_DIR, "dark", CALIBRATION_CAMERA)
        flats_dir = os.path.join(CALIBRATION_DIR, "flat", CALIBRATION_CAMERA)
        work_dir = os.path.join(root, "process")
        merged_dir = os.path.join(work_dir, "merged")

        siril.log(
            f"Config: {config_file} "
            f"({'loaded' if config_exists else 'not found - using defaults'}) | "
            f"drizzle {drizzle_scale:g}x | "
            f"pixfrac {pixel_fraction:.2f} | WFWHM {wfwhm_percent:g}% | "
            f"sigma {sigma_low:g}/{sigma_high:g} | "
            f"keep intermediates {keep_intermediates} | "
            f"result {result_name}",
            s.LogColor.BLUE,
        )

        if not os.path.isdir(lights_dir):
            raise FileNotFoundError("No lights/ directory found.")

        if not os.path.isdir(darks_dir):
            raise FileNotFoundError(
                f"No dark/ directory found at: {darks_dir}"
            )

        if not os.path.isdir(flats_dir):
            raise FileNotFoundError(
                f"No flat/ directory found at: {flats_dir}"
            )

        lights = [parse_light(x) for x in fits_files(lights_dir)]
        darks = [parse_dark(x) for x in fits_files(darks_dir)]

        if not lights:
            raise RuntimeError("No FITS lights found.")
        if not darks:
            raise RuntimeError("No master darks found.")

        # Process each night as its own unit. Within a night, split by
        # temperature as well as exposure, gain and filter so every light is
        # calibrated with the exact matching dark. Date is an outer processing
        # boundary, but is never used when selecting the dark master.
        nights = defaultdict(list)
        for light in lights:
            nights[light["date"]].append(light)

        groups = {}
        for date in sorted(nights):
            groups[date] = defaultdict(list)
            for light in nights[date]:
                key = (
                    light["temperature"],
                    round(light["exposure"], 3),
                    round(light["gain"], 3),
                    light["filter"],
                )
                groups[date][key].append(light)

        dark_for_group = {}
        dark_usage = []

        total_groups = sum(len(groups[date]) for date in groups)
        siril.log(
            f"Found {len(lights)} lights across {len(nights)} nights and "
            f"{total_groups} date/temp/exposure/gain/filter groups.",
            s.LogColor.GREEN,
        )
        siril.log(
            f"Found {len(darks)} master darks in {darks_dir}.",
            s.LogColor.GREEN,
        )
        siril.log(
            f"Using flats from {flats_dir}.",
            s.LogColor.GREEN,
        )

        for date in sorted(groups):
            siril.log(
                f"Night {date}: {len(groups[date])} calibration groups.",
                s.LogColor.BLUE,
            )

            for key, group in sorted(groups[date].items()):
                temperature, exposure, gain, filter_id = key

                dark = choose_dark(darks, exposure, gain, temperature)
                if dark is None:
                    raise RuntimeError(
                        f"No dark for {exposure:g}s, gain {gain:g}, "
                        f"{temperature:.1f}C."
                    )

                dark_for_group[(date, key)] = dark
                dark_usage.append((date, key, len(group), dark))

                delta = abs(dark["temperature"] - temperature)

                siril.log(
                    f"{date} | {exposure:g}s gain {gain:g} {temperature:.1f}C ir_{filter_id} | "
                    f"{len(group)} lights | -> {dark['temperature']:.1f}C dark | "
                    f"delta {delta:.1f}C",
                    s.LogColor.GREEN,
                )
                siril.log(
                    f"  Dark selected: {os.path.basename(dark['path'])}",
                    s.LogColor.GREEN,
                )

                if delta > DARK_TEMP_WARNING_C:
                    siril.log(
                        f"WARNING: dark temperature differs by {delta:.1f}C.",
                        s.LogColor.SALMON,
                    )

        remove(work_dir)
        os.makedirs(merged_dir, exist_ok=True)

        # Process each date independently, with temperature as part of the
        # calibration grouping. Each group is calibrated, registered, drizzled
        # and stacked independently; the resulting group stacks are then
        # aligned and combined into the final image.
        stack_results = []
        group_number = 0

        for date in sorted(groups):
            for key in sorted(groups[date]):
                group_number += 1
                temperature, exposure, gain, filter_id = key
                group = groups[date][key]
                dark = dark_for_group[(date, key)]

                tag = (
                    f"{date}_{exposure:g}s_g{gain:g}_{temperature:.1f}C_ir{filter_id}"
                )
                group_dir = os.path.join(work_dir, tag)
                os.makedirs(group_dir, exist_ok=True)

                siril.log(
                    f"[{group_number}/{total_groups}] "
                    f"Processing {date} | {exposure:g}s gain {gain:g} {temperature:.1f}C "
                    f"ir_{filter_id} ({len(group)} frames)",
                    s.LogColor.GREEN,
                )
                siril.log(
                    f"  Using dark: {os.path.basename(dark['path'])}",
                    s.LogColor.GREEN,
                )

                copy_as_sequence(
                    [x["path"] for x in group],
                    group_dir,
                )

                run(siril, "cd", group_dir)

                # Add the dark after creating the light sequence so Siril cannot
                # mistake it for another light frame.
                shutil.copy2(
                    dark["path"],
                    os.path.join(group_dir, "master_dark.fits"),
                )

                flat_files = fits_files(flats_dir)

                matching_flats = [
                    flat for flat in flat_files
                    if re.search(
                        rf"(?:^|_)ir_{re.escape(filter_id)}(?:_|\.)",
                        os.path.basename(flat),
                        re.IGNORECASE,
                    )
                ]

                if not matching_flats:
                    raise FileNotFoundError(
                        f"No master flat for ir_{filter_id} found in flats/."
                    )

                if len(matching_flats) > 1:
                    raise RuntimeError(
                        f"Expected exactly one master flat for ir_{filter_id}, "
                        f"found {len(matching_flats)}."
                    )

                flat_path = matching_flats[0]

                siril.log(
                    f"  Using flat: {flat_path}",
                    s.LogColor.GREEN,
                )

                shutil.copy2(
                    flat_path,
                    os.path.join(group_dir, "master_flat.fits"),
                )

                group_result_name = f"{result_name}_{tag}"

                if len(group) == 1:
                    # Siril does not expose a one-frame FITS file as a normal
                    # sequence, so calibrate the image directly. There is no
                    # registration, drizzle or rejection to perform on a
                    # single frame; the calibrated image becomes this group's
                    # integrated result and will be registered with the other
                    # group results in the final merge.
                    single_input = os.path.join(group_dir, "light_000001.fit")
                    single_output = os.path.join(group_dir, "cal_light_000001.fit")

                    siril.log(
                        f"Calibrating single frame for {tag}...",
                        s.LogColor.GREEN,
                    )
                    run(
                        siril,
                        "calibrate_single",
                        "light_000001.fit",
                        "-dark=master_dark.fits",
                        "-flat=master_flat.fits",
                        "-cfa",
                        "-debayer",
                        "-prefix=cal_",
                    )

                    if not os.path.exists(single_output):
                        raise RuntimeError(
                            f"Expected calibrated single frame was not created: "
                            f"{single_output}"
                        )

                    group_result = single_output
                    stack_results.append(group_result)
                    continue

                run(
                    siril,
                    "calibrate",
                    "light_",
                    "-dark=master_dark.fits",
                    "-flat=master_flat.fits",
                    "-cfa",
                    "-fitseq",
                    "-prefix=cal_",
                )

                # Register and drizzle this exposure/gain/filter group
                # independently. This keeps frames with different exposure
                # lengths out of the same registration/stacking population.
                siril.log(
                    f"Registering sequence for {tag}...",
                    s.LogColor.GREEN,
                )
                run(siril, "register", "cal_light_", "-2pass")

                siril.log(
                    f"Applying drizzle to {tag} ({drizzle_scale:g}x)...",
                    s.LogColor.GREEN,
                )
                run(
                    siril,
                    "seqapplyreg",
                    "cal_light_",
                    "-drizzle",
                    f"-scale={drizzle_scale:g}",
                    f"-pixfrac={pixel_fraction:.2f}",
                    f"-kernel={DRIZZLE_KERNEL}",
                )

                stack = [
                    "stack",
                    "r_cal_light_",
                    "rej",
                    f"{sigma_low:g}",
                    f"{sigma_high:g}",
                    "-norm=addscale",
                    "-maximize",
                    "-32b",
                ]

                if USE_WEIGHTED_FWHM:
                    stack.append("-weight=wfwhm")

                stack.extend(filter_args(wfwhm_percent))
                stack.append(f"-out={group_result_name}")

                siril.log(
                    f"Stacking {tag}...",
                    s.LogColor.GREEN,
                )
                run(siril, *stack)

                group_result = os.path.join(
                    group_dir,
                    f"{group_result_name}.fit",
                )

                if not os.path.exists(group_result):
                    raise RuntimeError(
                        f"Expected group stack was not created: {group_result}"
                    )

            stack_results.append(group_result)

        if not stack_results:
            raise RuntimeError("No exposure-group stacks were produced.")

        # If there is only one exposure/gain/filter group, its stack is already
        # the final result. Otherwise, make an ordinary FITS-image sequence
        # from the group stacks, register it, apply those transforms, and then
        # combine the registered group images.
        #
        # Do NOT use -fitseq here. A FITS sequence in Siril is a set of files
        # named basename_00001.fit, basename_00002.fit, etc. -fitseq creates a
        # single FITS cube, which is not what we want for this second-stage
        # registration.
        if len(stack_results) == 1:
            source_result = stack_results[0]
        else:
            run(siril, "cd", merged_dir)

            # Remove anything left by a previous failed run.
            for name in os.listdir(merged_dir):
                path = os.path.join(merged_dir, name)
                if os.path.isfile(path) and (
                    name.startswith("group_") or
                    name.startswith("r_group_") or
                    name.startswith("group_" + result_name)
                ):
                    remove(path)

            # Create a normal Siril FITS sequence. The naming pattern is what
            # makes group_ the sequence name.
            for index, stack_result in enumerate(stack_results, 1):
                target = os.path.join(
                    merged_dir,
                    f"group_{index:05d}.fit",
                )
                shutil.copy2(stack_result, target)

            siril.log(
                f"Registering {len(stack_results)} independent exposure "
                f"group stacks together...",
                s.LogColor.GREEN,
            )
            run(siril, "register", "group_", "-2pass")

            # register -2pass only calculates the transforms. seqapplyreg is
            # required to actually create r_group_*.fit.
            run(
                siril,
                "seqapplyreg",
                "group_",
                "-framing=max",
            )

            siril.log(
                "Combining independently stacked exposure groups...",
                s.LogColor.GREEN,
            )

            # There is no useful sigma rejection to perform here: these are
            # already integrated group images, not individual light frames.
            # Use mean stacking with no rejection. nbstack weights each group
            # by the number of source frames represented by that group.
            # This preserves the benefit of having more source frames in a
            # group without pretending the individual exposure lengths were
            # interchangeable during the original rejection stage.
            final_stack = [
                "stack",
                "r_group_",
                "mean",
                "none",
                "-norm=addscale",
                "-maximize",
                "-output_norm",
                "-32b",
                f"-out={result_name}",
            ]

            run(siril, *final_stack)

            source_result = os.path.join(
                merged_dir,
                f"{result_name}.fit",
            )

        root_result = os.path.join(
            root,
            f"{result_name}.fit",
        )

        if os.path.exists(source_result):
            shutil.copy2(source_result, root_result)

        run(siril, "cd", root)
        run(siril, "load", result_name)

        siril.log("==============================================", s.LogColor.GREEN)
        siril.log("Dwarf Mini multi-night stack COMPLETE", s.LogColor.GREEN)
        siril.log(f"Result: {root_result}", s.LogColor.GREEN)
        siril.log(f"Lights: {len(lights)}", s.LogColor.GREEN)
        siril.log(f"Calibration groups: {len(groups)}", s.LogColor.GREEN)
        siril.log(
            f"Drizzle: {drizzle_scale:g}x / pixfrac {pixel_fraction:.2f}",
            s.LogColor.GREEN,
        )

        siril.log("----------------------------------------------", s.LogColor.BLUE)
        siril.log("Dark used for each light group:", s.LogColor.BLUE)
        for date, key, count, dark in dark_usage:
            temperature, exposure, gain, filter_id = key
            siril.log(
                f"  {date} | {exposure:g}s gain {gain:g} {temperature:.1f}C ir_{filter_id} "
                f"({count} lights) -> {os.path.basename(dark['path'])}",
                s.LogColor.BLUE,
            )
        siril.log(
            "Frame filtering: "
            + (" ".join(filter_args(wfwhm_percent)) if filter_args(wfwhm_percent) else "none"),
            s.LogColor.GREEN,
        )

        if keep_intermediates:
            siril.log(
                f"Intermediate files kept in: {work_dir}",
                s.LogColor.BLUE,
            )
        else:
            remove(work_dir)
            siril.log("Intermediate files removed.", s.LogColor.BLUE)

    except Exception as exc:
        siril.log(f"PROCESSING FAILED: {exc}", s.LogColor.RED)
        raise

    finally:
        siril.disconnect()


if __name__ == "__main__":
    main()
