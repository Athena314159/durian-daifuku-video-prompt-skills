#!/usr/bin/env python3
import hashlib, json, tempfile
from pathlib import Path
from pipeline import validate_no_generation_docx_contract

def write(path,value): path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
def main():
  with tempfile.TemporaryDirectory() as td:
    root=Path(td); owners=['ADD001','SRC001','SRC002','SRC003','SRC004','SRC005','ADD002','SRC006','ADD003']; refs=[]
    for i,owner in enumerate(owners):
      p=root/f'assets/r{i}.png'; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(f'ref-{owner}'.encode())
      role='exact_source_reuse' if owner=='SRC006' else ('legacy_rejected_example' if owner=='ADD003' else 'legacy_composition_reference')
      refs.append({'owner_unit_id':owner,'asset_role':role,'path':str(p.relative_to(root)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'caption':owner})
    contract={'schema_version':'no-generation-prompt-docx-alignment-v1.0','status':'ready_for_prompt_compile_and_docx_alignment','approved_target_frame_count':0,'references':refs,'script_lock':{'effective_character_count':249,'unit_concatenation_equivalent':True,'spoken_shot_concatenation_equivalent':True}}
    write(root/'planning/no_generation_prompt_docx_alignment_contract.json',contract)
    project={'execution_tier':'prompt_only','document_delivery_mode':'no_generation_prompt_docx_alignment'}; issues=[]
    validate_no_generation_docx_contract(root,project,issues); assert not issues,issues
    contract['references'][0]['asset_role']='approved_target_frame'; write(root/'planning/no_generation_prompt_docx_alignment_contract.json',contract); issues=[]
    validate_no_generation_docx_contract(root,project,issues); assert any(x['code']=='NO_GENERATION_REFERENCE_ROLE_FORBIDDEN' for x in issues)
    export_source=(Path(__file__).parent/'export_no_generation_docx.py').read_text(); align_source=(Path(__file__).parent/'align_exports.py').read_text(); reuse_source=(Path(__file__).parent/'audit_asset_reuse.py').read_text()
    assert 'approved_target_frame_count' in export_source and 'align_no_generation_mode' in align_source
    assert 'w:br' in align_source and 'NO_GENERATION_MODE' in reuse_source and 'gallery user approval' in reuse_source
  print('no-generation DOCX mode tests passed')
if __name__=='__main__': raise SystemExit(main())
