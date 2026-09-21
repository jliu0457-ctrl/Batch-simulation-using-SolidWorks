"""Build explicitly provisional CAD-dimension perturbations, never design samples."""
import json, math, shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
inv=json.loads((ROOT/'artifacts/cad_inventory_typed.json').read_text(encoding='utf-8-sig'))
parts={key:next(d['file'] for d in inv['documents'] if key in d['file']) for key in ['03阀体','04阀轴','08大垫片','09密封圈','10压板','11蝶板']}
candidate={
'c_mm': {'base':32.,'step':.32,'links':{'03阀体':['D2@草图3'],'08大垫片':['D10@草图1'],'09密封圈':['D3@草图1'],'10压板':['D2@草图1'],'11蝶板':['D2@草图1']}},
'e_mm': {'base':3.7,'step':.037,'links':{'03阀体':['D30@草图4','D4@草图6'],'09密封圈':['D9@草图1'],'11蝶板':['D1@草图1','D1@基准面2']}},
'phi_deg': {'base':8.25,'step':.1,'links':{'03阀体':['D3@草图5'],'08大垫片':['D5@草图1'],'09密封圈':['D5@草图1'],'10压板':['D4@草图1'],'11蝶板':['D2@草图3']}},
'alpha_deg': {'base':35.5,'step':.1,'links':{'03阀体':['D2@草图5'],'08大垫片':['D7@草图1'],'09密封圈':['D6@草图1'],'10压板':['D7@草图1'],'11蝶板':['D4@草图3']}},
'Dmax_mm': {'base':194.37,'step':1.9437,'links':{'03阀体':['D5@草图5'],'08大垫片':['D6@草图1'],'09密封圈':['D7@草图1'],'10压板':['D5@草图1'],'11蝶板':['D3@草图3']}},
'bm_mm': {'base':7.5,'step':.075,'links':{'09密封圈':['D2@草图1']}},
'ds_mm': {'base':45.,'step':.45,'links':{'03阀体':['D25@草图4'],'04阀轴':['D4@草图1'],'09密封圈':['D8@草图1'],'11蝶板':['D2@草图2']}},
}
baseline={k:v['base'] for k,v in candidate.items()}
trials=[('baseline',baseline.copy())]
for k,item in candidate.items():
 for sign,suffix in [(-1,'minus'),(1,'plus')]:
  values=baseline.copy();values[k]+=sign*item['step'];trials.append((k+'_'+suffix,values))
plan={'status':'provisional_dimensions_not_design_mapping','training_ready':False,'notes':['Dmax candidate is cone-construction circle, not yet physical maximum disc diameter','c reference plane is seal midplane; bm stack not validated','No solver or training labels in these experiments'],'candidate_links':candidate,'experiments':[]}
destroot=ROOT/'working/candidate_trials/v2'
if destroot.exists(): raise SystemExit('Refusing overwrite of existing candidate_trials')
for name,values in trials:
 folder=destroot/name
 folder.mkdir(parents=True)
 for source in (ROOT/'working/baseline').glob('*.SLD*'):
  shutil.copy2(source,folder/source.name)
 ops=[]
 for key,item in candidate.items():
  for part,params in item['links'].items():
   factor=math.pi/180 if key.endswith('_deg') else .001
   if key=='alpha_deg' and part!='08大垫片':factor*=.5
   for param in params:
    ops.append({'variable_candidate':key,'file':parts[part],'parameter':param,'baseline_SI':item['base']*factor,'value_SI':values[key]*factor})
 plan['experiments'].append({'id':name,'folder':str(folder),'candidate_input_values':values,'operations':ops})
(ROOT/'config/candidate_trials.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'experiments_prepared':len(trials),'training_ready':False}))
