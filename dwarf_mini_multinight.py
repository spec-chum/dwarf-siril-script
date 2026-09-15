import configparser
import os
import re
import shutil
from collections import defaultdict
from datetime import datetime

import sirilpy as s


# ---------------------------- settings -------------------------------------

DRIZZLE_KERNEL = "square"


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
    filter_wfwhm=100.0,
    filter_round=100.0,
    filter_background=100.0,
    filter_star_count=100.0,
):
    args = []

    if filter_wfwhm < 100:
        args.append(f"-filter-wfwhm={filter_wfwhm}%")
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
        use_weighted_fwhm = config.getboolean(
            "processing", "use_weighted_fwhm", fallback=True
        )
        filter_wfwhm = config.getfloat(
            "processing", "filter_wfwhm", fallback=100.0
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


        base_dir = root

        lights_dir = os.path.join(root, "lights")
        darks_dir = os.path.join(CALIBRATION_DIR, "dark", CALIBRATION_CAMERA)
        flats_dir = os.path.join(CALIBRATION_DIR, "flat", CALIBRATION_CAMERA)
        work_dir = os.path.join(root, "process")
        merged_dir = os.path.join(work_dir, "merged")

        siril.log(
            f"Config: {config_file} "
            f"({'loaded' if config_exists else 'not found - using defaults'}) | "
            f"drizzle {drizzle_scale:g}x | "
            f"pixfrac {pixel_fraction:.2f} | WFWHM {filter_wfwhm:g}% | "
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

        # Calibration is grouped because each temperature/exposure/gain/filter
        # combination may need a different master dark. Once calibrated, the
        # individual lights no longer need to remain separated by calibration
        # group. Collect all calibrated lights for each filter into one
        # sequence, then register and stack that complete sequence once.
        combined_dirs = {}
        calibrated_sequences = {"1": [], "2": []}
        sequence_counts = {}
        for filter_id, filter_label in (("1", "astro"), ("2", "duoband")):
            combined_dir = os.path.join(merged_dir, filter_label, "calibrated")
            remove(combined_dir)
            os.makedirs(combined_dir, exist_ok=True)
            combined_dirs[filter_id] = combined_dir

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
                f"Calibrating {exposure:g}s gain {gain:g} {temperature:.1f}C "
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

            # Keep the Bayer CFA data intact for Bayer drizzle. Siril's drizzle
            # workflow requires color-camera inputs to remain undebayered.
            if len(group) == 1:
                # calibrate expects a sequence; handle a one-frame group with
                # calibrate_single, then add that calibrated frame to the same
                # combined filter sequence as every other light.
                run(
                    siril,
                    "calibrate_single",
                    "light_000001.fit",
                    "-dark=master_dark.fits",
                    "-flat=master_flat.fits",
                    "-cc=dark",
                    "3",
                    "3",
                    "-cfa",
                    "-equalize_cfa",
                    "-prefix=cal_",
                )
            else:
                run(
                    siril,
                    "calibrate",
                    "light_",
                    "-dark=master_dark.fits",
                    "-flat=master_flat.fits",
                    "-cc=dark",
                    "3",
                    "3",
                    "-cfa",
                    "-equalize_cfa",
                    "-fitseq",
                    "-prefix=cal_",
                )

            # Keep the calibrated sequence in its group directory.
            cal_seq = os.path.join(group_dir, "cal_light_")
            if len(group) == 1:
                # calibrate_single produces a normal FITS image. Put that one
                # image in a clean directory before converting it to a one-frame
                # sequence, so the master dark/flat in group_dir cannot also be
                # picked up by CONVERT.
                single_dir = os.path.join(group_dir, "single_sequence")
                remove(single_dir)
                os.makedirs(single_dir, exist_ok=True)
                shutil.copy2(
                    os.path.join(group_dir, "cal_light_000001.fit"),
                    os.path.join(single_dir, "cal_light_000001.fit"),
                )
                run(siril, "cd", single_dir)
                run(siril, "convert", "cal_light_", "-fitseq")
                cal_seq = os.path.join(single_dir, "cal_light_")

            # The .seq file is Siril's canonical sequence descriptor.
            # Do not assume a particular FITS storage layout.
            if not os.path.isfile(cal_seq + ".seq"):
                raise RuntimeError(
                    f"Expected calibrated sequence was not created: {cal_seq}.seq"
                )

            calibrated_sequences[filter_id].append(cal_seq)
            sequence_counts[cal_seq] = len(group)

        if not any(calibrated_sequences.values()):
            raise RuntimeError("No calibrated light sequences were produced.")

        # Merge the calibrated sequences for each filter. Calibration remains
        # grouped because each group may need a different dark; after that, the
        # individual calibrated frames are registered and stacked together.
        output_results = {}

        for filter_id, filter_label in (("1", "astro"), ("2", "duoband")):
            seqs = calibrated_sequences[filter_id]
            if not seqs:
                continue

            combined_dir = combined_dirs[filter_id]
            run(siril, "cd", combined_dir)
            count = sum(sequence_counts[seq] for seq in seqs)

            if len(seqs) == 1:
                combined_seq = seqs[0]
                siril.log(
                    f"Using single calibrated {filter_label} sequence "
                    f"({count} individual lights)...",
                    s.LogColor.GREEN,
                )
            else:
                siril.log(
                    f"Merging {len(seqs)} calibrated {filter_label} sequences "
                    f"({count} individual lights)...",
                    s.LogColor.GREEN,
                )
                run(siril, "merge", *seqs, "combined_")
                combined_seq = os.path.join(combined_dir, "combined_")

            siril.log(
                f"Registering + drizzling all {count} {filter_label} calibrated lights "
                f"({drizzle_scale:g}x)...",
                s.LogColor.GREEN,
            )
            run(siril, "register", combined_seq,
                f"-scale={drizzle_scale:g}", "-transf=homography",
                "-minpairs=10", "-maxstars=2000", "-drizzle",
                f"-pixfrac={pixel_fraction:.2f}", f"-kernel={DRIZZLE_KERNEL}")

            registered_seq = os.path.join(
                os.path.dirname(combined_seq),
                "r_" + os.path.basename(combined_seq)
            )
            stack = ["stack", registered_seq, "rej", "winsorized",
                     f"{sigma_low:g}", f"{sigma_high:g}",
                     "-norm=addscale", "-output_norm", "-32b"]
            if use_weighted_fwhm:
                stack.append("-weight=wfwhm")
            stack.extend(filter_args(filter_wfwhm, filter_round,
                                     filter_background, filter_star_count))
            filter_result_name = f"{result_name}_{filter_label}"
            stack.append(f"-out={filter_result_name}")
            siril.log(f"Stacking all {count} calibrated {filter_label} lights...", s.LogColor.GREEN)
            run(siril, *stack)
            # Keep the finished result outside work_dir so cleanup cannot delete it.
            stacked_path = os.path.join(combined_dir, f"{filter_result_name}.fit")
            final_path = os.path.join(root, f"{filter_result_name}.fit")
            if not os.path.exists(stacked_path):
                raise RuntimeError(f"Stack completed but output file was not found: {stacked_path}")
            shutil.copy2(stacked_path, final_path)
            output_results[filter_id] = final_path

        # If both filter results exist, optionally register them against each
        # other and use common framing so their pixels line up exactly.
        if align_filters and len(output_results) == 2:
            align_dir = os.path.join(merged_dir, "align_filters")
            remove(align_dir)
            os.makedirs(align_dir, exist_ok=True)
            run(siril, "cd", align_dir)

            astro_path = output_results["1"]
            duoband_path = output_results["2"]
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
            filter_wfwhm,
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
            # Return Siril to the original working directory before deleting the
            # temporary tree. This prevents Windows WinError 32 when Siril still
            # has the last processing directory open.
            run(siril, "cd", base_dir)
            remove(work_dir)
            siril.log("Intermediate files removed.", s.LogColor.BLUE)

    except Exception as exc:
        siril.log(f"PROCESSING FAILED: {exc}", s.LogColor.RED)
        raise

    finally:
        siril.disconnect()


if __name__ == "__main__":
    main()
