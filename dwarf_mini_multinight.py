import configparser
import os
import re
import shutil
from collections import defaultdict
from datetime import datetime

import sirilpy as s


# ---------------------------- settings -------------------------------------

DRIZZLE_KERNEL = "square"


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
        if dark["exposure"] == exposure
        and dark["gain"] == gain
    ]

    if not candidates:
        return None

    # Temperature is deliberately not an exact-match requirement. The Dwarf's
    # dark library may not contain a master at every captured temperature.
    # Prefer the closest temperature; if tied, prefer the larger master.
    return min(
        candidates,
        key=lambda dark: (
            abs(dark["temperature"] - temperature),
            -dark["stack_count"],
        ),
    )


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


def filter_args(
    filter_fwhm=100.0,
    wfwhm_percent=100.0,
    filter_round=100.0,
    filter_background=100.0,
    filter_star_count=100.0,
):
    args = []

    if filter_fwhm < 100:
        args.append(f"-filter-fwhm={filter_fwhm}%")
    if wfwhm_percent < 100:
        args.append(f"-filter-wfwhm={wfwhm_percent}%")
    if filter_round < 100:
        args.append(f"-filter-round={filter_round}%")
    if filter_background < 100:
        args.append(f"-filter-bkg={filter_background}%")
    if filter_star_count < 100:
        args.append(f"-filter-nbstars={filter_star_count}%")

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
        filter_fwhm = config.getfloat(
            "processing", "filter_fwhm", fallback=100.0
        )
        filter_round = config.getfloat(
            "processing", "filter_round", fallback=100.0
        )
        filter_background = config.getfloat(
            "processing", "filter_background", fallback=100.0
        )
        filter_star_count = config.getfloat(
            "processing", "filter_star_count", fallback=100.0
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
        align_filters = config.getboolean(
            "processing", "align_filters", fallback=True
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
            f"align filters {align_filters} | "
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

        # Group the entire archive by shot statistics. The capture date is
        # deliberately ignored: matching exposure/gain/temperature/filter
        # frames from different nights belong to the same calibration group.
        groups = defaultdict(list)
        for light in lights:
            key = (
                light["temperature"],
                light["exposure"],
                light["gain"],
                light["filter"],
            )
            groups[key].append(light)

        dark_for_group = {}
        dark_usage = []

        total_groups = len(groups)
        siril.log(
            f"Found {len(lights)} lights and {total_groups} "
            f"temperature/exposure/gain/filter groups.",
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

        for key, group in sorted(groups.items()):
            temperature, exposure, gain, filter_id = key

            dark = choose_dark(darks, exposure, gain, temperature)
            if dark is None:
                raise RuntimeError(
                    f"No dark for {exposure:g}s, gain {gain:g}."
                )

            dark_for_group[key] = dark
            dark_usage.append((key, len(group), dark))

            delta = abs(dark["temperature"] - temperature)

            siril.log(
                f"{exposure:g}s gain {gain:g} {temperature:.1f}C ir_{filter_id} | "
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

        # Process each calibration group independently. Groups can contain
        # frames from any number of nights; date is not a grouping dimension.
        stack_results = {"1": [], "2": []}
        group_number = 0

        for key in sorted(groups):
            group_number += 1
            temperature, exposure, gain, filter_id = key
            group = groups[key]
            dark = dark_for_group[key]

            tag = f"{exposure:g}s_g{gain:g}_{temperature:.1f}C_ir{filter_id}"
            group_dir = os.path.join(work_dir, tag)
            os.makedirs(group_dir, exist_ok=True)

            siril.log(
                f"[{group_number}/{total_groups}] "
                f"Processing {exposure:g}s gain {gain:g} {temperature:.1f}C "
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
                # A single frame cannot benefit from registration/drizzle or
                # rejection. Debayer it so it can participate in the final
                # filter-specific merge.
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

                stack_results[filter_id].append(single_output)
                continue

            # Keep the Bayer CFA data intact for Bayer drizzle. Siril's drizzle
            # workflow requires color-camera inputs to remain undebayered.
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

            stack.extend(
                filter_args(
                    filter_fwhm,
                    wfwhm_percent,
                    filter_round,
                    filter_background,
                    filter_star_count,
                )
            )
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

            stack_results[filter_id].append(group_result)

        if not any(stack_results.values()):
            raise RuntimeError("No exposure-group stacks were produced.")

        # Merge Astro and Duo-Band separately. Each group is already integrated,
        # so the second stage uses a mean with nbstack weighting rather than
        # another rejection pass.
        output_results = {}

        for filter_id, filter_label in (("1", "astro"), ("2", "duoband")):
            results = stack_results[filter_id]
            if not results:
                continue

            if len(results) == 1:
                source_result = results[0]
            else:
                filter_merged_dir = os.path.join(merged_dir, filter_label)
                remove(filter_merged_dir)
                os.makedirs(filter_merged_dir, exist_ok=True)

                run(siril, "cd", filter_merged_dir)

                for index, stack_result in enumerate(results, 1):
                    target = os.path.join(
                        filter_merged_dir,
                        f"group_{index:05d}.fit",
                    )
                    shutil.copy2(stack_result, target)

                siril.log(
                    f"Registering {len(results)} {filter_label} group stacks...",
                    s.LogColor.GREEN,
                )
                run(siril, "register", "group_", "-2pass")
                run(
                    siril,
                    "seqapplyreg",
                    "group_",
                    "-framing=max",
                )

                filter_result_name = f"{result_name}_{filter_label}"
                final_stack = [
                    "stack",
                    "r_group_",
                    "mean",
                    "none",
                    "-norm=addscale",
                    "-maximize",
                    "-output_norm",
                    "-32b",
                    "-weight=nbstack",
                    f"-out={filter_result_name}",
                ]
                run(siril, *final_stack)

                source_result = os.path.join(
                    filter_merged_dir,
                    f"{filter_result_name}.fit",
                )

            root_result = os.path.join(
                root,
                f"{result_name}_{filter_label}.fit",
            )
            shutil.copy2(source_result, root_result)
            output_results[filter_label] = root_result

        run(siril, "cd", root)

        # If both filter results exist, optionally register them against each
        # other and use common framing so their pixels line up exactly.
        if align_filters and len(output_results) == 2:
            align_dir = os.path.join(merged_dir, "align_filters")
            remove(align_dir)
            os.makedirs(align_dir, exist_ok=True)
            run(siril, "cd", align_dir)

            astro_path = output_results["astro"]
            duoband_path = output_results["duoband"]
            shutil.copy2(astro_path, os.path.join(align_dir, "group_00001.fit"))
            shutil.copy2(duoband_path, os.path.join(align_dir, "group_00002.fit"))

            siril.log(
                "Aligning Astro and Duo-Band results...",
                s.LogColor.GREEN,
            )
            run(siril, "register", "group_", "-2pass")
            run(siril, "seqapplyreg", "group_", "-framing=min")

            aligned_astro = os.path.join(align_dir, "r_group_00001.fit")
            aligned_duoband = os.path.join(align_dir, "r_group_00002.fit")

            if not os.path.exists(aligned_astro) or not os.path.exists(aligned_duoband):
                raise RuntimeError("Filter alignment did not produce both aligned results.")

            shutil.copy2(aligned_astro, astro_path)
            shutil.copy2(aligned_duoband, duoband_path)
            run(siril, "cd", root)
            siril.log(
                "Astro and Duo-Band results aligned to common framing.",
                s.LogColor.GREEN,
            )

        # Load the first available result so the script leaves Siril showing
        # a useful output. Both filter-specific files remain on disk.
        first_label = next(iter(output_results))
        run(
            siril,
            "load",
            os.path.splitext(os.path.basename(output_results[first_label]))[0],
        )

        siril.log("==============================================", s.LogColor.GREEN)
        siril.log("Dwarf Mini multi-night stack COMPLETE", s.LogColor.GREEN)
        for label, path in output_results.items():
            siril.log(f"Result ({label}): {path}", s.LogColor.GREEN)
        siril.log(f"Lights: {len(lights)}", s.LogColor.GREEN)
        siril.log(f"Calibration groups: {len(groups)}", s.LogColor.GREEN)
        siril.log(
            f"Drizzle: {drizzle_scale:g}x / pixfrac {pixel_fraction:.2f}",
            s.LogColor.GREEN,
        )

        siril.log("----------------------------------------------", s.LogColor.BLUE)
        siril.log("Dark used for each light group:", s.LogColor.BLUE)
        for key, count, dark in dark_usage:
            temperature, exposure, gain, filter_id = key
            siril.log(
                f"  {exposure:g}s gain {gain:g} {temperature:.1f}C ir_{filter_id} "
                f"({count} lights) -> {os.path.basename(dark['path'])}",
                s.LogColor.BLUE,
            )
        filter_settings = filter_args(
            filter_fwhm,
            wfwhm_percent,
            filter_round,
            filter_background,
            filter_star_count,
        )
        siril.log(
            "Frame filtering: "
            + (" ".join(filter_settings) if filter_settings else "none"),
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
