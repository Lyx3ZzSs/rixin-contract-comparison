import { expect, it, vi } from 'vitest';
import { getPreviewMetadata } from './api';
const { fetchMock }=vi.hoisted(()=>({fetchMock:vi.fn()}));
vi.mock('./authFetch',()=>({authorizedFetch:fetchMock,ApiError:Error}));
const doc={source_revision:'abc',page_count:1,pages:[{page_no:1,width_points:800,height_points:600,rotation:90}]};
const valid={schema_version:1,coordinate_space:'display_pdf_points',documents:{original:doc,compare:doc}};
it('accepts display geometry with rotation',async()=>{fetchMock.mockResolvedValue(new Response(JSON.stringify(valid)));expect(await getPreviewMetadata('task')).toEqual(valid)});
it.each([
 {...valid,coordinate_space:'unrotated'},
 {...valid,documents:{original:{...doc,page_count:2},compare:doc}},
 {...valid,documents:{original:{...doc,pages:[{...doc.pages[0],height_points:-2}]},compare:doc}},
 {...valid,documents:{original:{...doc,pages:[{...doc.pages[0],page_no:2}]},compare:doc}},
])('rejects incompatible or unsafe layout metadata',async data=>{fetchMock.mockResolvedValue(new Response(JSON.stringify(data)));await expect(getPreviewMetadata('task')).rejects.toThrow('PDF 页面尺寸数据无效')});
