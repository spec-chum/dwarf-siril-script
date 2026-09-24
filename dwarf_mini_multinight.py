import configparser
import math
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


def measure_image_noise(siril):
    """Return one background-noise estimate for the currently loaded image."""
    image = siril.get_image(with_pixels=True)
    if image is None or image.data is None or image.channels < 1:
        raise RuntimeError("Cannot retrieve pixel data from the loaded master.")

    channel_noises = []
    for channel in range(image.channels):
        # Compute directly from the pixels rather than requesting Siril's
        # cached image statistics: immediately after LOAD in Siril 1.4 those
        # cached fields can still be zero. The estimator sigma-clips
        # first-order pixel differences, limiting contamination by structure.
        noise = float(image.estimate_noise(image.get_channel(channel)))
        if math.isfinite(noise) and noise > 0:
            channel_noises.append(noise)

    if not channel_noises:
        raise RuntimeError("Could not measure positive noise in the loaded master.")

    # Use a single scalar weight for the RGB image while accounting for noise
    # in every channel. For mono images this reduces to that channel's noise.
    return math.sqrt(
        sum(noise * noise for noise in channel_noises) / len(channel_noises)
    )


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
        align_results = config.getboolean(
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
            f"align results {align_results} | "
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
        # combination may need a different master dark. Once calibrated, merge
        # groups only when their filter and exposure match. Different exposure
        # lengths must be stacked separately because saturated stars cannot be
        # normalized reliably against unsaturated stars during rejection.
        combined_dirs = {}
        calibrated_sequences = defaultdict(list)
        sequence_counts = {}

        filter_labels = {"1": "astro", "2": "duoband"}
        for _, exposure, _, filter_id in groups:
            stack_key = (filter_id, exposure)
            if stack_key in combined_dirs:
                continue

            combined_dir = os.path.join(
                merged_dir,
                filter_labels[filter_id],
                f"{exposure:g}s",
                "calibrated",
            )
            remove(combined_dir)
            os.makedirs(combined_dir, exist_ok=True)
            combined_dirs[stack_key] = combined_dir

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

            stack_key = (filter_id, exposure)
            calibrated_sequences[stack_key].append(cal_seq)
            sequence_counts[cal_seq] = len(group)

        if not any(calibrated_sequences.values()):
            raise RuntimeError("No calibrated light sequences were produced.")

        # Merge calibrated sequences only within a matching filter/exposure
        # pair. Calibration groups at different temperatures or gains may still
        # share a stack once they have been calibrated with their own darks.
        output_results = {}
        stack_livetimes = {}

        for stack_key in sorted(
            calibrated_sequences,
            key=lambda item: (item[0], item[1]),
        ):
            filter_id, exposure = stack_key
            filter_label = filter_labels[filter_id]
            seqs = calibrated_sequences[stack_key]
            combined_dir = combined_dirs[stack_key]
            run(siril, "cd", combined_dir)
            count = sum(sequence_counts[seq] for seq in seqs)

            combined_seq = "combined_"

            if len(seqs) == 1:
                siril.log(
                    f"Using single calibrated {filter_label} {exposure:g}s sequence "
                    f"({count} individual lights)...",
                    s.LogColor.GREEN,
                )

                source_base = seqs[0]
                source_file = None

                for extension in (".fit", ".fits"):
                    candidate = source_base + extension
                    if os.path.isfile(candidate):
                        source_file = candidate
                        break

                if source_file is None:
                    raise RuntimeError(
                        f"Calibrated FITS sequence file was not found: {source_base}"
                    )

                shutil.copy2(
                    source_file,
                    os.path.join(
                        combined_dir,
                        combined_seq + os.path.splitext(source_file)[1],
                    ),
                )
            else:
                siril.log(
                    f"Combining {len(seqs)} calibrated {filter_label} "
                    f"{exposure:g}s sequences...",
                    s.LogColor.GREEN,
                )
                run(siril, "merge", *seqs, combined_seq)

            siril.log(
                f"Registering + drizzling all {count} {filter_label} "
                f"{exposure:g}s calibrated lights "
                f"({drizzle_scale:g}x)...",
                s.LogColor.GREEN,
            )
            run(siril, "register", combined_seq,
                f"-scale={drizzle_scale:g}", "-transf=homography",
                "-minpairs=10", "-maxstars=2000", "-drizzle",
                f"-pixfrac={pixel_fraction:.2f}", f"-kernel={DRIZZLE_KERNEL}")

            registered_seq = "r_" + combined_seq
            stack = ["stack", registered_seq, "rej", "winsorized",
                     f"{sigma_low:g}", f"{sigma_high:g}",
                     "-norm=addscale", "-output_norm", "-32b"]
            if use_weighted_fwhm:
                stack.append("-weight=wfwhm")
            stack.extend(filter_args(filter_wfwhm, filter_round,
                                     filter_background, filter_star_count))
            filter_result_name = (
                f"{result_name}_{filter_label}_{exposure:g}s"
            )
            stack.append(f"-out={filter_result_name}")
            siril.log(
                f"Stacking all {count} calibrated {filter_label} "
                f"{exposure:g}s lights...",
                s.LogColor.GREEN,
            )
            run(siril, *stack)
            # Keep the finished result outside work_dir so cleanup cannot delete it.
            stacked_path = os.path.join(combined_dir, f"{filter_result_name}.fit")
            final_path = os.path.join(root, f"{filter_result_name}.fit")
            if not os.path.exists(stacked_path):
                raise RuntimeError(f"Stack completed but output file was not found: {stacked_path}")

            # Siril writes the actual integration represented by the stack to
            # LIVETIME. This accounts for frames excluded by stack filtering.
            run(siril, "load", filter_result_name)
            header = siril.get_image_fits_header(return_as="dict") or {}
            try:
                livetime = float(header.get("LIVETIME", count * exposure))
            except (TypeError, ValueError):
                livetime = count * exposure
            if livetime <= 0:
                livetime = count * exposure

            shutil.copy2(stacked_path, final_path)
            output_results[stack_key] = final_path
            stack_livetimes[stack_key] = livetime

        # Register every completed filter/exposure stack together so all final
        # results have identical geometry and can be blended directly.
        if align_results and len(output_results) > 1:
            align_dir = os.path.join(merged_dir, "align_results")
            remove(align_dir)
            os.makedirs(align_dir, exist_ok=True)
            run(siril, "cd", align_dir)

            aligned_results = sorted(
                output_results.items(),
                key=lambda item: (item[0][0], item[0][1]),
            )
            for index, (_, result_path) in enumerate(aligned_results, 1):
                shutil.copy2(
                    result_path,
                    os.path.join(align_dir, f"group_{index:05d}.fit"),
                )

            siril.log(
                f"Aligning all {len(aligned_results)} filter/exposure results...",
                s.LogColor.GREEN,
            )
            run(siril, "register", "group_", "-2pass")
            run(siril, "seqapplyreg", "group_", "-framing=min")

            for index, (_, result_path) in enumerate(aligned_results, 1):
                aligned_path = os.path.join(
                    align_dir,
                    f"r_group_{index:05d}.fit",
                )
                if not os.path.exists(aligned_path):
                    raise RuntimeError(
                        "Result alignment did not produce every aligned image."
                    )
                shutil.copy2(aligned_path, result_path)

            run(siril, "cd", root)
            siril.log(
                "All filter/exposure results aligned to common framing.",
                s.LogColor.GREEN,
            )

        # Linear-match the aligned exposure masters to the shortest exposure,
        # then average them using inverse-variance weights measured from the
        # matched masters. The measured master noise already reflects total
        # integration time, accepted-frame count and within-stack weighting, so
        # multiplying by LIVETIME again would count integration time twice.
        combined_results = {}
        for filter_id, filter_label in filter_labels.items():
            filter_stacks = sorted(
                (
                    (stack_key, result_path)
                    for stack_key, result_path in output_results.items()
                    if stack_key[0] == filter_id
                ),
                key=lambda item: item[0][1],
            )
            if len(filter_stacks) < 2:
                continue
            if not align_results:
                siril.log(
                    f"Skipping {filter_label} exposure blend because "
                    "align_filters is disabled.",
                    s.LogColor.SALMON,
                )
                continue
            if len(filter_stacks) > 10:
                raise RuntimeError(
                    "Cannot blend more than 10 exposure stacks with Siril PixelMath."
                )

            blend_dir = os.path.join(merged_dir, "blend_exposures", filter_label)
            remove(blend_dir)
            os.makedirs(blend_dir, exist_ok=True)
            run(siril, "cd", blend_dir)

            blend_inputs = []
            reference_name = "blend_00001"
            for index, (stack_key, result_path) in enumerate(filter_stacks, 1):
                source_name = f"blend_{index:05d}"
                shutil.copy2(
                    result_path,
                    os.path.join(blend_dir, source_name + ".fit"),
                )

                run(siril, "load", source_name)
                if index == 1:
                    blend_name = source_name
                else:
                    blend_name = f"matched_{index:05d}"
                    run(
                        siril,
                        "linear_match",
                        reference_name,
                        "0.001",
                        "0.92",
                    )
                    run(siril, "save", blend_name)

                noise = measure_image_noise(siril)
                blend_inputs.append((blend_name, 1.0 / (noise * noise), noise))

            total_weight = sum(weight for _, weight, _ in blend_inputs)
            expression = " + ".join(
                f"${name}$ * {weight:.12g}"
                for name, weight, _ in blend_inputs
            )
            expression = f"({expression}) / {total_weight:.12g}"

            combined_name = f"{result_name}_{filter_label}"
            siril.log(
                f"Blending {len(blend_inputs)} {filter_label} exposure stacks "
                "using measured inverse-variance weights...",
                s.LogColor.GREEN,
            )
            for (stack_key, _), (_, weight, noise) in zip(
                filter_stacks,
                blend_inputs,
            ):
                siril.log(
                    f"  {stack_key[1]:g}s master: noise={noise:.6g}, "
                    f"weight={weight / total_weight:.2%}, "
                    f"livetime={stack_livetimes[stack_key]:g}s",
                    s.LogColor.GREEN,
                )

            run(siril, "set32bits")
            run(siril, "pm", f'"{expression}"')
            run(siril, "save", combined_name)

            blended_path = os.path.join(blend_dir, combined_name + ".fit")
            final_blended_path = os.path.join(root, combined_name + ".fit")
            if not os.path.exists(blended_path):
                raise RuntimeError(
                    f"Exposure blend was not created: {blended_path}"
                )
            shutil.copy2(blended_path, final_blended_path)
            combined_results[filter_id] = final_blended_path

        # Load the first available result so the script leaves Siril showing
        # a useful output. Prefer a combined exposure result when available.
        run(siril, "cd", root)
        display_path = (
            next(iter(combined_results.values()))
            if combined_results
            else next(iter(output_results.values()))
        )
        run(
            siril,
            "load",
            os.path.splitext(os.path.basename(display_path))[0],
        )

        siril.log("==============================================", s.LogColor.GREEN)
        siril.log("Dwarf Mini multi-night stack COMPLETE", s.LogColor.GREEN)
        for (filter_id, exposure), path in output_results.items():
            siril.log(
                f"Result ({filter_labels[filter_id]}, {exposure:g}s): {path}",
                s.LogColor.GREEN,
            )
        for filter_id, path in combined_results.items():
            siril.log(
                f"Combined result ({filter_labels[filter_id]}): {path}",
                s.LogColor.GREEN,
            )
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
