#!/usr/bin/env python3
"""
NIH PDF Cleaner - GUI Tool
Removes hyperlinks from PDF documents for NIH proposal submissions.
Turns blue text black.
Removes underlines associated with hyperlinks.
Highlights changes yellow and unallowable URLs orange in the tracked changes version

Author: NIH Hyperlinks Project
Version: 2.0.0
"""

from pypdf.generic import ArrayObject, ContentStream, DecodedStreamObject, DictionaryObject, FloatObject, NameObject, NumberObject
from pypdf import PdfReader, PdfWriter
from datetime import datetime
from tkinter import messagebox
import tkinter as tk
import copy
from pathlib import Path
import logging
import os


# Debug switch for underline matching: set to True to log underline decisions.
DEBUG_UNDERLINE_MATCHING = False
# Optional focus rectangle for underline debug; set to a tuple like (x0, y0, x1, y1).
DEBUG_UNDERLINE_FOCUS_RECT = None


def get_user_input():
    """Get input and output directory paths from user via GUI."""
    result = [None]

    def create_gui():
        root = tk.Tk()
        root.title("NIH PDF Cleaner - Input Paths")

        tk.Label(root, text="Input Directory:").grid(
            row=0, column=0, padx=10, pady=10, sticky="e")
        input_entry = tk.Entry(root, width=50)
        input_entry.grid(row=0, column=1, padx=10, pady=10)

        tk.Label(root, text="Output Directory:").grid(
            row=1, column=0, padx=10, pady=10, sticky="e")
        output_entry = tk.Entry(root, width=50)
        output_entry.grid(row=1, column=1, padx=10, pady=10)

        def on_ok():
            input_dir = input_entry.get().strip().strip('"')
            output_dir = output_entry.get().strip().strip('"')
            if not input_dir or not output_dir:
                messagebox.showerror(
                    "Error", "Both input and output directories must be provided.")
                return
            result[0] = (input_dir, output_dir, True)
            root.destroy()

        def on_cancel():
            result[0] = None
            root.destroy()

        tk.Button(root, text="Cancel", command=on_cancel, bg="red",
                  fg="white").grid(row=3, column=0, pady=10)
        tk.Button(root, text="OK", command=on_ok, bg="green",
                  fg="white").grid(row=3, column=1, pady=10)

        root.mainloop()

    create_gui()
    return result[0]


def setup_logging(verbose=False):
    """Configure logging based on verbosity level."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )


def validate_directories(input_dir, output_dir):
    """Validate input and output directories."""
    input_dir = os.path.abspath(input_dir)
    output_dir = os.path.abspath(output_dir)

    if not os.path.exists(input_dir):
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")

    if not os.path.isdir(input_dir):
        raise NotADirectoryError(f"Input path is not a directory: {input_dir}")

    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    return Path(input_dir), Path(output_dir)


def find_pdf_files(input_dir):
    """Find all PDF files in the input directory."""
    pdf_files = []
    for file_path in input_dir.rglob('*.pdf'):
        if file_path.is_file():
            pdf_files.append(file_path)

    return sorted(pdf_files)


def normalize_rect(rect):
    """Normalize a PDF rectangle to(x0, y0, x1, y1)."""
    try:
        values = [float(v) for v in rect]
    except (TypeError, ValueError):
        return None
    if len(values) != 4:
        return None
    x0, y0, x1, y1 = values
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def normalize_re_rect(rect):
    """Normalize an re command rectangle from (x, y, width, height) to(x0, y0, x1, y1)."""
    try:
        x, y, w, h = [float(v) for v in rect]
    except (TypeError, ValueError):
        return None
    x1 = x + w
    y1 = y + h
    return (min(x, x1), min(y, y1), max(x, x1), max(y, y1))


def line_in_rect(x0, y0, x1, y1, rect, tolerance=20.0):
    """Return True if a horizontal line is inside or near a rectangle."""
    rx0, ry0, rx1, ry1 = rect
    if abs(y0 - y1) > tolerance:
        return False
    y = (y0 + y1) / 2
    return y >= ry0 - tolerance and y <= ry1 + tolerance and min(x0, x1) >= rx0 - tolerance and max(x0, x1) <= rx1 + tolerance


def rect_in_rect(inner, outer, tolerance=20.0):
    """Return True if a small rectangle is inside or near a larger rectangle."""
    ix0, iy0, ix1, iy1 = inner
    ox0, oy0, ox1, oy1 = outer
    return ix0 >= ox0 - tolerance and ix1 <= ox1 + tolerance and iy0 >= oy0 - tolerance and iy1 <= oy1 + tolerance


def add_transparency_extgstate(page, opacity=0.5):
    """Ensure the page has an ExtGState entry for fill/stroke opacity."""
    resources = page.get("/Resources")
    if resources is None:
        resources = DictionaryObject()
        page[NameObject("/Resources")] = resources
    if hasattr(resources, "get_object"):
        resources = resources.get_object()

    extgstate = resources.get("/ExtGState")
    if extgstate is None:
        extgstate = DictionaryObject()
        resources[NameObject("/ExtGState")] = extgstate
    elif hasattr(extgstate, "get_object"):
        extgstate = extgstate.get_object()

    gs_name = NameObject("/GSOpacity")
    if gs_name not in extgstate:
        extgstate[gs_name] = DictionaryObject({
            NameObject("/ca"): FloatObject(opacity),
            NameObject("/CA"): FloatObject(opacity),
            NameObject("/BM"): NameObject("/Normal")
        })

    return gs_name


# *****Remove hyperlinks section*****
def remove_hyperlinks_from_pdf(input_path, output_path, create_tracked_changes=False, tracked_changes_path=None, write_clean=True, write_tracked=True):
    """
 Remove all hyperlinks from a PDF document and convert blue text to black.
  Optionally create a tracked changes version highlighting changes in yellow.

   Args:
        input_path(Path): Path to input PDF file
        output_path(Path): Path to output PDF file
        create_tracked_changes(bool): Whether to create a tracked changes version
        tracked_changes_path(Path): Path for tracked changes PDF(required if create_tracked_changes=True and write_tracked=True)
        write_clean(bool): Whether to write the clean output
        write_tracked(bool): Whether to write the tracked changes output

    Returns:
        dict: Processing results with hyperlink counts per page
    """
    try:
        logging.info(f"Processing: {input_path.name}")

        reader = PdfReader(str(input_path))
        writer = PdfWriter()

        total_hyperlinks_removed = 0
        total_color_changes = 0
        page_results = {}

        def process_page_version(page_obj, page_index):
            annots = page_obj.get("/Annots")
            removed_count = 0
            link_rects = []
            skip_rects = []

            if annots:
                if hasattr(annots, "get_object"):
                    annots = annots.get_object()

                kept_annots = []
                if isinstance(annots, ArrayObject):
                    annot_refs = list(annots)
                else:
                    annot_refs = [annots]

                for annot_ref in annot_refs:
                    annot = annot_ref.get_object()
                    if annot.get("/Subtype") == "/Link":
                        is_orcid = False
                        if "/A" in annot:
                            action = annot["/A"]
                            if hasattr(action, "get_object"):
                                action = action.get_object()
                            if "/URI" in action:
                                uri = action["/URI"]
                                if isinstance(uri, bytes):
                                    uri = uri.decode("utf-8", errors="ignore")
                                if "orcid.org" in str(uri).lower():
                                    is_orcid = True

                        if "/Rect" in annot:
                            rect = normalize_rect(annot["/Rect"])
                            if rect:
                                if is_orcid:
                                    skip_rects.append(rect)
                                    kept_annots.append(annot_ref)
                                else:
                                    link_rects.append(rect)
                                    removed_count += 1
                            else:
                                if is_orcid:
                                    kept_annots.append(annot_ref)
                                else:
                                    removed_count += 1
                        else:
                            if is_orcid:
                                kept_annots.append(annot_ref)
                            else:
                                removed_count += 1
                    else:
                        kept_annots.append(annot_ref)

                if kept_annots:
                    page_obj[NameObject("/Annots")] = ArrayObject(kept_annots)
                elif "/Annots" in page_obj:
                    del page_obj["/Annots"]

            color_changed = process_text_colors_and_underlines(
                page_obj, reader, link_rects, skip_rects, 'clean')
            return removed_count, color_changed, len(link_rects)

        for page_index, page in enumerate(reader.pages, start=1):
            removed_count, clean_color_changed, _ = process_page_version(
                page, page_index)
            writer.add_page(page)

            if clean_color_changed:
                total_color_changes += 1
                logging.info(
                    f"  Page {page_index}: Converted hyperlinked blue text to black")

            total_hyperlinks_removed += removed_count
            page_results[page_index] = removed_count

            if removed_count > 0:
                logging.info(
                    f"  Page {page_index}: Removed {removed_count} hyperlinks")

        with open(output_path, "wb") as output_file:
            writer.write(output_file)

        logging.info(
            f"Completed: {output_path.name} (Total hyperlinks removed: {total_hyperlinks_removed}, "
            f"pages color-adjusted: {total_color_changes})"
        )

        return {
            'success': True,
            'total_hyperlinks': total_hyperlinks_removed,
            'pages_processed': len(page_results),
            'page_details': page_results,
            'color_adjusted_pages': total_color_changes
        }

    except Exception as e:
        error_msg = f"Error processing {input_path.name}: {str(e)}"
        logging.error(error_msg)
        return {
            'success': False,
            'error': error_msg,
            'total_hyperlinks': 0,
            'pages_processed': 0,
            'page_details': {}
        }

# *****Blue to black section***
# Blue to black 1/6


def process_content_stream(obj, reader, link_rects=None, skip_rects=None, mode='clean'):
    """Process a page or XObject content stream and handle colors and underlines based on mode."""
    if link_rects is None:
        link_rects = []
    if skip_rects is None:
        skip_rects = []

    if "/Contents" not in obj or not link_rects:
        return False

    content = ContentStream(obj["/Contents"], reader)
    changed = False
    new_operations = []
    operations = list(content.operations)

    # Track text position and last blue color
    current_text_x = 0.0
    current_text_y = 0.0
    last_blue_index = -1
    in_link_context = False
    debug = DEBUG_UNDERLINE_MATCHING
    focus_rect = DEBUG_UNDERLINE_FOCUS_RECT

    def is_thin_underline_rectangle(rect):
        _, y0, _, y1 = rect
        height = abs(y1 - y0)
        return 0 < height <= 6.0

    def is_link_name(obj):
        if obj == b"/Link" or obj == "/Link":
            return True
        try:
            return str(obj) == "/Link"
        except Exception:
            return False

    def is_link_bdc_sequence(start_index):
        for k in range(start_index + 1, min(start_index + 60, len(operations))):
            next_operands, next_operator = operations[k]
            if next_operator == b"BDC" and len(next_operands) >= 1 and is_link_name(next_operands[0]):
                if debug:
                    print(
                        f"DEBUG: Found Link BDC after op {start_index} at op {k}")
                return True
            # Continue scanning through text blocks and artifacts, since underlines can appear before the actual Link BDC.
            if next_operator == b"EMC" and debug:
                print(
                    f"DEBUG: Encountered EMC while searching for Link BDC at op {k}")
        if debug:
            print(f"DEBUG: No Link BDC found after op {start_index}")
        return False

    def is_path_paint_operator(operator):
        return operator in (b"f", b"F", b"f*", b"S", b"s", b"B", b"B*", b"b", b"b*", b"W", b"W*", b"n")

    i = 0
    while i < len(operations):
        operands, operator = operations[i]
        removed_op = False

        # Update text matrix
        if operator == b"Tm" and len(operands) == 6:
            try:
                current_text_x = float(operands[4])
                current_text_y = float(operands[5])
            except (TypeError, ValueError):
                pass
        elif operator == b"Td" and len(operands) == 2:
            try:
                current_text_x += float(operands[0])
                current_text_y += float(operands[1])
            except (TypeError, ValueError):
                pass
        elif operator == b"TD" and len(operands) == 2:
            try:
                current_text_x += float(operands[0])
                current_text_y += float(operands[1])
            except (TypeError, ValueError):
                pass

        # Check for text operations after blue color
        elif operator in (b"Tj", b"TJ", b"'"):
            if last_blue_index >= 0:
                tolerance = 50.0  # Increased tolerance for matching
                for link_rect in link_rects:
                    lx0, ly0, lx1, ly1 = link_rect
                    if (lx0 - tolerance <= current_text_x <= lx1 + tolerance and
                            ly0 - tolerance <= current_text_y <= ly1 + tolerance):
                        # Check if this link rect is in the skip list (e.g., ORCID)
                        is_skipped = any(rectangles_overlap(
                            link_rect, skip_rect, tolerance=10.0) for skip_rect in skip_rects)
                        if not is_skipped:
                            # Change the blue color
                            blue_operands, blue_operator = operations[last_blue_index]
                            if mode == 'clean':
                                new_color = black_color_components(
                                    len(blue_operands))
                            else:
                                new_color = yellow_color_components(
                                    len(blue_operands))
                            blue_operands[:] = [
                                NumberObject(v) for v in new_color]
                            changed = True
                            last_blue_index = -1  # Reset after changing
                        break

        # Track blue color commands
        elif operator in (b"rg", b"RG", b"sc", b"SC", b"scn", b"SCN") and len(operands) in (3, 4):
            if is_blue_rgb(operands[:3]):
                last_blue_index = i
            else:
                last_blue_index = -1
        elif operator in (b"k", b"K") and len(operands) == 4:
            if is_blue_cmyk(operands):
                last_blue_index = i
            else:
                last_blue_index = -1
        elif operator == b"ET":
            last_blue_index = -1
        elif operator == b"BDC" and len(operands) >= 1 and is_link_name(operands[0]):
            in_link_context = True
        elif operator == b"EMC" and in_link_context:
            in_link_context = False

        # Existing logic for drawing operations (keep for completeness)
        if operator in (b"rg", b"RG", b"sc", b"SC", b"scn", b"SCN") and len(operands) in (3, 4) and last_blue_index == i:
            # Look ahead for drawing operations that overlap with link rects
            should_convert = False

            for j in range(i + 1, min(i + 100, len(operations))):
                next_operands, next_operator = operations[j]

                if next_operator == b"re" and len(next_operands) == 4:
                    rect = normalize_rect(next_operands)
                    if rect:
                        for link_rect in link_rects:
                            if rectangles_overlap(rect, link_rect, tolerance=5.0):
                                should_convert = True
                                break

                elif next_operator == b"m" and j + 1 < len(operations):
                    next2_operands, next2_operator = operations[j + 1]
                    if next2_operator == b"l":
                        try:
                            x0, y0 = [float(v) for v in next_operands]
                            x1, y1 = [float(v) for v in next2_operands]
                        except (TypeError, ValueError):
                            pass
                        else:
                            for link_rect in link_rects:
                                if line_in_rect(x0, y0, x1, y1, link_rect):
                                    should_convert = True
                                    break

                elif next_operator == b"c" and len(next_operands) == 6:
                    try:
                        x, y = [float(next_operands[4]),
                                float(next_operands[5])]
                        for link_rect in link_rects:
                            lx0, ly0, lx1, ly1 = link_rect
                            if lx0 - 5 <= x <= lx1 + 5 and ly0 - 5 <= y <= ly1 + 5:
                                should_convert = True
                                break
                    except (TypeError, ValueError):
                        pass

                elif next_operator in (b"rg", b"RG", b"sc", b"SC", b"scn", b"SCN", b"k", b"K", b"ET"):
                    break

                if should_convert:
                    break

            if should_convert:
                if mode == 'clean':
                    new_color = black_color_components(len(operands))
                else:
                    new_color = yellow_color_components(len(operands))
                operands[:] = [NumberObject(v) for v in new_color]
                changed = True
                last_blue_index = -1  # Reset since we converted

        elif operator in (b"k", b"K") and len(operands) == 4 and last_blue_index == i:
            should_convert = False

            for j in range(i + 1, min(i + 100, len(operations))):
                next_operands, next_operator = operations[j]

                if next_operator == b"re" and len(next_operands) == 4:
                    rect = normalize_rect(next_operands)
                    if rect:
                        for link_rect in link_rects:
                            if rectangles_overlap(rect, link_rect, tolerance=5.0):
                                should_convert = True
                                break

                elif next_operator == b"m" and j + 1 < len(operations):
                    next2_operands, next2_operator = operations[j + 1]
                    if next2_operator == b"l":
                        try:
                            x0, y0 = [float(v) for v in next_operands]
                            x1, y1 = [float(v) for v in next2_operands]
                        except (TypeError, ValueError):
                            pass
                        else:
                            for link_rect in link_rects:
                                if line_in_rect(x0, y0, x1, y1, link_rect):
                                    should_convert = True
                                    break

                elif next_operator == b"c" and len(next_operands) == 6:
                    try:
                        x, y = [float(next_operands[4]),
                                float(next_operands[5])]
                        for link_rect in link_rects:
                            lx0, ly0, lx1, ly1 = link_rect
                            if lx0 - 5 <= x <= lx1 + 5 and ly0 - 5 <= y <= ly1 + 5:
                                should_convert = True
                                break
                    except (TypeError, ValueError):
                        pass

                elif next_operator in (b"rg", b"RG", b"sc", b"SC", b"scn", b"SCN", b"k", b"K", b"ET"):
                    break

                if should_convert:
                    break

            if should_convert:
                if mode == 'clean':
                    new_color = black_color_components(4)
                else:
                    new_color = yellow_color_components(4)
                operands[:] = [NumberObject(v) for v in new_color]
                changed = True
                last_blue_index = -1

        # Remove hyperlink rectangles and underlines
        elif operator == b"re" and len(operands) == 4:
            rect = normalize_re_rect(operands)
            if rect and is_thin_underline_rectangle(rect):
                should_remove = False
                for link_rect in link_rects:
                    if rectangles_overlap(rect, link_rect, tolerance=10.0):
                        # Check if this link rect is in the skip list (e.g., ORCID)
                        is_skipped = any(rectangles_overlap(
                            link_rect, skip_rect, tolerance=2.0) for skip_rect in skip_rects)
                        if not is_skipped and (in_link_context or is_link_bdc_sequence(i) or rect_in_rect(rect, link_rect, tolerance=5.0)):
                            if mode == 'clean':
                                should_remove = True
                                removed_op = True
                                changed = True
                            break
                if debug and (focus_rect is None or rect == focus_rect):
                    print(
                        f"DEBUG: underline rect={rect}, link_overlap={should_remove}, in_link_context={in_link_context}")
            if removed_op:
                next_i = i + 1
                while next_i < len(operations) and is_path_paint_operator(operations[next_i][1]):
                    if debug:
                        print(
                            f"DEBUG: removing path paint op {operations[next_i][1]} after rect at op {i}")
                    next_i += 1
                i = next_i
                continue

        elif operator == b"m" and i + 2 < len(operations):
            next_operands, next_operator = operations[i + 1]
            next2_operands, next2_operator = operations[i + 2]
            if next_operator == b"l" and next2_operator in (b"S", b"s", b"B", b"B*"):
                try:
                    x0, y0 = [float(v) for v in operands]
                    x1, y1 = [float(v) for v in next_operands]
                except (TypeError, ValueError):
                    x0 = y0 = x1 = y1 = None
                if x0 is not None:
                    line_rect = (min(x0, x1), min(y0, y1),
                                 max(x0, x1), max(y0, y1))
                    for link_rect in link_rects:
                        if rectangles_overlap(line_rect, link_rect, tolerance=10.0):
                            # Check if this link rect is in the skip list (e.g., ORCID)
                            is_skipped = any(rectangles_overlap(
                                link_rect, skip_rect, tolerance=2.0) for skip_rect in skip_rects)
                            if not is_skipped and (in_link_context or is_link_bdc_sequence(i) or rect_in_rect(line_rect, link_rect, tolerance=5.0)):
                                if mode == 'clean':
                                    removed_op = True
                                    changed = True
                                break
                    if removed_op:
                        next_i = i + 2
                        while next_i < len(operations) and is_path_paint_operator(operations[next_i][1]):
                            if debug:
                                print(
                                    f"DEBUG: removing path paint op {operations[next_i][1]} after line at op {i}")
                            next_i += 1
                        i = next_i
                        continue

        if not removed_op:
            new_operations.append((operands, operator))
        i += 1

    if changed:
        content.operations = new_operations
        obj[NameObject("/Contents")] = content

    return changed


def rectangles_overlap(rect1, rect2, tolerance=3.0):
    """Check if two rectangles overlap or are very close."""
    x0_1, y0_1, x1_1, y1_1 = rect1
    x0_2, y0_2, x1_2, y1_2 = rect2

    # Check if rectangles overlap (with tolerance)
    return not (x1_1 + tolerance < x0_2 or x1_2 + tolerance < x0_1 or
                y1_1 + tolerance < y0_2 or y1_2 + tolerance < y0_1)

# Blue to black 2/6


def is_blue_rgb(components):
    """Return True if the color components represent a blue-ish RGB color."""
    if len(components) != 3:
        return False

    try:
        r, g, b = [float(c) for c in components]
    except (TypeError, ValueError):
        return False

    # Detect both pure blue and visually blue shades.
    if b < 0.5:
        return False

    if b >= 0.95 and r < 0.1 and g < 0.1:
        return True

    if b >= max(r, g) + 0.2 and r <= 0.4 and g <= 0.5:
        return True

    return False

# Blue to black 3/6


def is_blue_cmyk(components):
    """Return True if the color components represent pure blue in CMYK."""
    if len(components) != 4:
        return False

    try:
        values = [float(c) for c in components]
    except (TypeError, ValueError):
        return False

    return all(abs(v - target) < 1e-6 for v, target in zip(values, [1.0, 1.0, 0.0, 0.0]))

# Blue to black 4/6


def black_color_components(length):
    """Return black color components for the same color space length."""
    if length == 3:
        return [0, 0, 0]
    if length == 4:
        return [0, 0, 0, 1]
    return [0, 0, 0]


def yellow_color_components(length):
    """Return yellow color components for the same color space length."""
    if length == 3:
        return [1, 1, 0]  # RGB yellow
    if length == 4:
        return [0, 0, 1, 0]  # CMYK yellow
    return [1, 1, 0]

# Blue to black 5/6


def process_xobject_resources(resources, reader, link_rects=None, skip_rects=None, mode='clean'):
    """Recursively process XObject resources for color and underline handling."""
    if link_rects is None:
        link_rects = []
    if skip_rects is None:
        skip_rects = []

    if not resources or "/XObject" not in resources:
        return False

    xobjects = resources["/XObject"]
    if hasattr(xobjects, "get_object"):
        xobjects = xobjects.get_object()

    changed = False
    for xobj_ref in xobjects.values():
        xobj = xobj_ref.get_object()
        if xobj.get("/Subtype") == "/Form":
            changed |= process_content_stream(
                xobj, reader, link_rects, skip_rects, mode)
            if "/Resources" in xobj:
                changed |= process_xobject_resources(
                    xobj["/Resources"], reader, link_rects, skip_rects, mode)

    return changed

# Blue to black 6/6


def process_text_colors_and_underlines(page, reader, link_rects=None, skip_rects=None, mode='clean'):
    """Convert blue text color commands and handle underlines in a PDF page based on mode."""
    if link_rects is None:
        link_rects = []
    if skip_rects is None:
        skip_rects = []
    changed = process_content_stream(
        page, reader, link_rects, skip_rects, mode)
    if "/Resources" in page:
        changed |= process_xobject_resources(
            page["/Resources"], reader, link_rects, skip_rects, mode)
    return changed


# Process all PDF files in the input directory
def process_pdfs(input_dir, output_dir, overwrite=False, create_tracked_changes=False):
    """
    Process all PDF files in the input directory.

    Args:
        input_dir(Path): Input directory path
        output_dir(Path): Output directory path
        overwrite(bool): Whether to overwrite existing files
        create_tracked_changes(bool): Whether to create tracked changes versions

    Returns:
        dict: Summary of processing results
    """
    # Find all PDF files
    pdf_files = find_pdf_files(input_dir)

    if not pdf_files:
        logging.error(f"No PDF files found in {input_dir}")
        return {'total_files': 0, 'processed': 0, 'failed': 0, 'total_hyperlinks': 0}

    logging.info(f"Found {len(pdf_files)} PDF file(s) to process")

    processed = 0
    failed = 0
    total_hyperlinks_removed = 0
    results = []

    for input_file in pdf_files:
        # Create output filename with _clean suffix and timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix_addition = f"_clean__{timestamp}"
        extension = input_file.suffix

        max_filename_length = 255
        max_stem_length = max_filename_length - \
            len(suffix_addition) - len(extension)
        stem = input_file.stem
        if len(stem) > max_stem_length:
            stem = stem[:max_stem_length]

        output_filename = f"{stem}{suffix_addition}{extension}"
        output_file = output_dir / output_filename

        if output_file.exists() and not overwrite:
            logging.warning(
                f"Skipping {input_file.name} - output file already exists: {output_file.name}")
            failed += 1
            results.append({
                'input_file': str(input_file),
                'output_file': str(output_file),
                'success': False,
                'error': 'Output file already exists',
                'total_hyperlinks': 0,
                'pages_processed': 0
            })
            continue
        result = remove_hyperlinks_from_pdf(
            input_file, output_file)

        if result['success']:
            processed += 1
            total_hyperlinks_removed += result['total_hyperlinks']
        else:
            failed += 1

        results.append({
            'input_file': str(input_file),
            'output_file': str(output_file),
            'success': result['success'],
            'error': result.get('error', ''),
            'total_hyperlinks': result['total_hyperlinks'],
            'pages_processed': result['pages_processed']
        })

    # Print summary
    logging.info("=" * 60)
    logging.info("PROCESSING SUMMARY")
    logging.info("=" * 60)
    logging.info(f"Total PDF files found: {len(pdf_files)}")
    logging.info(f"Successfully processed: {processed}")
    logging.info(f"Failed/Skipped: {failed}")
    logging.info(f"Total hyperlinks removed: {total_hyperlinks_removed}")
    logging.info("=" * 60)

    return {
        'total_files': len(pdf_files),
        'processed': processed,
        'failed': failed,
        'total_hyperlinks': total_hyperlinks_removed,
        'results': results
    }


def inspect_pdf_file(input_pdf_path, output_pdf_path=None):
    """Inspect a PDF and optionally its processed output file for annotations and operators."""
    input_path = Path(input_pdf_path)
    print(f"Inspecting input PDF: {input_path}")

    reader = PdfReader(str(input_path))
    page = reader.pages[0]

    annots = page.get('/Annots')
    print('Annots:', type(annots), annots)
    if annots:
        if hasattr(annots, 'get_object'):
            annots = annots.get_object()
        for i, annot_ref in enumerate(annots):
            annot = annot_ref.get_object()
            print('Annot', i, 'subtype', annot.get(
                '/Subtype'), 'rect', annot.get('/Rect'))

    content = page['/Contents']
    cs = ContentStream(content, reader)
    ops = list(cs.operations)
    print('Total ops:', len(ops))

    for i, (operands, operator) in enumerate(ops):
        if 40 <= i <= 50 or 60 <= i <= 70 or 75 <= i <= 80:
            print(i, operator, operands)

    if output_pdf_path:
        out_path = Path(output_pdf_path)
        if out_path.exists():
            print('\nOutput PDF exists:', out_path)
            out_reader = PdfReader(str(out_path))
            out_page = out_reader.pages[0]
            out_annots = out_page.get('/Annots')
            print('Output annots:', out_annots)
            if out_annots:
                if hasattr(out_annots, 'get_object'):
                    out_annots = out_annots.get_object()
                for i, annot_ref in enumerate(out_annots):
                    annot = annot_ref.get_object()
                    print('Out Annot', i, 'subtype', annot.get(
                        '/Subtype'), 'rect', annot.get('/Rect'))
        else:
            print('Output PDF not found:', out_path)

# Do the actual parsing


def main():
    """Main entry point for the GUI tool."""
    # Get user input
    paths = get_user_input()
    if paths is None:
        logging.info("Operation cancelled by user")
        sys.exit(0)

    input_dir_str, output_dir_str, *_ = paths

    setup_logging(verbose=False)

    try:
        input_dir, output_dir = validate_directories(
            input_dir_str, output_dir_str)

        logging.info("NIH PDF Cleaner v2.0.0")
        logging.info(f"Input directory: {input_dir}")
        logging.info(f"Output directory: {output_dir}")
        logging.info("-" * 60)

        results = process_pdfs(input_dir, output_dir, overwrite=False)

        if results['failed'] > 0:
            sys.exit(1)

    except Exception as e:
        messagebox.showerror("Error", f"Fatal error: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
