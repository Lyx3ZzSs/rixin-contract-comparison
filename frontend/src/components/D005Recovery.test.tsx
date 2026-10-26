import { render } from '@testing-library/react';
import { expect, it } from 'vitest';
import { getPageHighlights, PdfHighlightLayer } from './PdfDocumentViewer';
import type { DiffItem, EvidenceBox } from '../types';
it('renders 47 repeated legacy table regions once as an outline, including selection', () => {
 const box: EvidenceBox = {page_no: 1, bbox: {x0:21,y0:397.5,x1:576,y1:559},method:'table_cell',text:'',highlight_type:'MODIFY'};
 const d: DiffItem = {diff_id:'D005',diff_type:'MODIFY',source_type:'table',clause_no:'',title:'表格结构待复核',original_text:'',compare_text:'',original_snippet:'',compare_snippet:'',readable_change:'',original_evidence:Array.from({length:47},()=>({...box})),compare_evidence:[box],match_score_details:{decision_reason:'TABLE_STRUCTURE_UNRESOLVED'}};
 const hs=getPageHighlights([d],'original',1);
 expect(hs).toHaveLength(1);
 expect(getPageHighlights([d],'compare',1)).toHaveLength(1);
 const v=render(<PdfHighlightLayer highlights={hs} activeDiffId="D005" pageSize={{width:595.44,height:841.68}} zoom={1} onActivateDiff={()=>{}}/>);
 expect(v.container.querySelectorAll('rect')).toHaveLength(1);
 expect(v.container.querySelector('.active.observation')).not.toBeNull();
});
