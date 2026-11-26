"""Replay a real fine-MDT case with QGIS Python and the installed TauDEM runtime.

Inputs are read-only. All generated products and the JSON report go to --output.
The accepted D8 is the raster used by the original fine-basin delineation.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('reference-dem', 'reference-d8', 'fine-dem', 'accepted-d8', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--x', type=float, required=True)
    parser.add_argument('--y', type=float, required=True)
    args = parser.parse_args()
    from qgis.core import QgsApplication
    from osgeo import gdal
    from caumax_consulta.terrain import analyze_basin
    from caumax_consulta.dem_hydrology import check_feasibility, _flow_accumulation
    from caumax_consulta.refinement_snap import select_reference_outlet
    from caumax_consulta.drainage_controller import _refinement_reference, _state
    from caumax_consulta.taudem_engine import prepare_taudem
    from caumax_consulta.hydro_export import export_dem, export_directions, export_network
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    args.output.mkdir(parents=True, exist_ok=True)
    original_hash = hashlib.sha256(args.fine_dem.read_bytes()).hexdigest()
    base = analyze_basin(args.reference_d8, args.reference_dem, args.x, args.y, upstream_only=False)
    radius = math.hypot(base.cell_width, base.cell_height)
    reference = dict(geometry_wkb=base.geometry_wkb, main_course_wkb=base.main_course_wkb,
        crs_wkt=base.crs_wkt, cell_width=base.cell_width, cell_height=base.cell_height,
        selection_policy='nearest_reference_chain_cell')
    dataset = gdal.Open(str(args.accepted_d8))
    counts = _flow_accumulation(dataset.GetRasterBand(1))
    cell, snap = select_reference_outlet(args.accepted_d8, counts, args.x, args.y, radius,
        reference, reference_area_m2=base.area_m2)
    dataset = counts = None
    accepted = analyze_basin(args.accepted_d8, args.fine_dem, cell.x, cell.y)
    accepted = replace(accepted, selected_x=args.x, selected_y=args.y,
        diagnostics=dict(accepted.diagnostics, outlet_snap=snap))
    reference = _refinement_reference(accepted, base)
    result = prepare_taudem(args.fine_dem, args.x, args.y, args.output / 'taudem',
        reference_area_m2=base.area_m2, reference_cell_width=base.cell_width,
        reference_cell_height=base.cell_height, outlet_analysis=accepted,
        refinement_reference=reference,
        progress=lambda message, percent: print(f'{percent:.0f}% {message}', flush=True))
    paths = []
    for operation, name in ((export_dem, 'MDT.tif'), (export_directions, 'D8_ESRI.tif'),
                            (export_network, 'Red_drenaje.shp')):
        destination = args.output / name
        operation(result, destination)
        paths.append(str(destination.resolve()))
    assert hashlib.sha256(args.fine_dem.read_bytes()).hexdigest() == original_hash
    report = dict(selected_point=[args.x, args.y], original_area_m2=base.area_m2,
        accepted_area_m2=accepted.area_m2, taudem_area_m2=result.analysis.area_m2,
        accepted_outlet=[accepted.outlet_x, accepted.outlet_y],
        taudem_outlet=[result.analysis.outlet_x, result.analysis.outlet_y],
        selection_policy=reference['selection_policy'], feasibility=check_feasibility(args.fine_dem),
        segments=len(_state(result, True, 'caumax')['features']),
        diagnostics=result.analysis.diagnostics, outputs=paths, dem_sha256=original_hash)
    (args.output / 'report.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('taudem_area_m2', 'segments', 'outputs')}, indent=2))


if __name__ == '__main__':
    main()
