"""Read independent seven-variable acceptance evidence without calling CAD."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ('c_mm', 'e_mm', 'phi_deg', 'alpha_deg', 'Dmax_mm', 'bm_mm', 'ds_mm')

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def summarize(path):
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    rows = []
    for row in data.get('results', []):
        result = row.get('result', {})
        issues = []
        if result.get('completed') is not True or result.get('physical_mapping_verified') is not True:
            issues.append('geometry_not_verified')
        if result.get('error'):
            issues.append(result['error'])
        caliper = result.get('caliper_after_reopen') or {}
        design = result.get('input') or {}
        if result.get('completed'):
            try:
                lo, hi = caliper['lower_mm'], caliper['upper_mm']
                if not 0 <= hi-lo <= .001000001 or max(abs(lo-design['Dmax_mm']), abs(hi-design['Dmax_mm'])) > .001000001:
                    issues.append('Dmax_interval_outside_tolerance')
                measured = result['analytic_geometry_after_reopen']
                for field in FIELDS:
                    if field != 'Dmax_mm' and abs(measured[field]-design[field]) > (1e-5 if field.endswith('_deg') else .001):
                        issues.append('reopened_geometry_mismatch:'+field)
                sweep = result['angle_sweep']
                if [item['requested_angle_deg'] for item in sweep] != [45]:
                    issues.append('opening_checks_incomplete')
                for item in sweep:
                    if item.get('completed') is not True or item.get('feature_errors'):
                        issues.append('opening_check_failed')
                    if abs(item['actual_angle_deg']-item['requested_angle_deg']) > 1e-5:
                        issues.append('opening_angle_mismatch')
                    if item['geometry']['axis_offset_mm'] > .001:
                        issues.append('opening_axis_displaced')
                if result['reopened_axis']['axis_offset_mm'] > .001:
                    issues.append('reopened_axis_displaced')
                readback = result['persisted_readback']
                if len(readback) != 33:
                    issues.append('expected_33_persisted_parameters')
                if any(abs(w['value_SI']-w['expected_SI']) > 1e-8 for w in readback):
                    issues.append('persisted_dimension_mismatch')
                folder = Path(row['folder']).resolve()
                if not folder.is_relative_to(ROOT / 'working' / 'seven_variable_trials'):
                    raise ValueError('trial folder outside task')
                files = result['cad_hashes_after']
                names = [f['file'] for f in files]
                if len(set(names)) != 15 or sum(n.lower().endswith('.sldasm') for n in names) != 1 or sum(n.lower().endswith('.sldprt') for n in names) != 14:
                    issues.append('expected_fifteen_cad_files')
                for item in files:
                    if Path(item['file']).name != item['file']:
                        raise ValueError('invalid CAD filename')
                    if sha(folder/item['file']) != item['sha256'].lower():
                        issues.append('CAD_file_changed:'+item['file'])
            except (KeyError, TypeError, OSError, ValueError) as exc:
                issues.append(str(exc))
        rows.append({'id': row.get('id'), 'verified_by_summary': not issues,
                     'Dmax_target_mm': design.get('Dmax_mm'),
                     'Dmax_lower_mm': caliper.get('lower_mm'), 'Dmax_upper_mm': caliper.get('upper_mm'),
                     'issues': issues})
    ids = [r['id'] for r in rows]
    batch_issues = []
    if len(ids) != len(set(ids)):
        batch_issues.append('duplicate_trial_ids')
    if data.get('suite') == 'full':
        expected = {'baseline'} | {f+'_'+s for f in FIELDS for s in ('minus','plus')}
        if set(ids) != expected:
            batch_issues.append('full_trial_set_incomplete')
    for key in ('template_unchanged','command_in_progress_restored'):
        if data.get(key) is not True:
            batch_issues.append(key+'_not_confirmed')
    if data.get('template_hashes_before') != data.get('template_hashes_after'):
        batch_issues.append('template_final_hashes_not_confirmed')
    passed = sum(r['verified_by_summary'] for r in rows)
    accepted = (data.get('status') == 'execution_finished' and not data.get('error')
                and not batch_issues and passed == data.get('requested_trials')
                and data.get('all_requested_geometry_trials_passed') is True)
    return {'run_name': data.get('run_name'), 'status': data.get('status'),
            'requested_trials': data.get('requested_trials'), 'reported_trials': len(rows),
            'independently_verified_trials': passed, 'batch_accepted': accepted,
            'batch_issues': batch_issues, 'training_ready': False, 'rows': rows}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    result = summarize(args.report)
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n'
    if args.out:
        output = args.out.resolve()
        if not output.is_relative_to(ROOT) or output.suffix.lower() != '.json':
            raise ValueError('output must be a new JSON inside task root')
        with output.open('x', encoding='utf-8') as stream:
            stream.write(encoded)
    print(encoded)
