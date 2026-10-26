import { describe, expect, it } from 'vitest';
import { counterpart } from './eventAlignment';
describe('event page correspondence', () => {
  const entries=[{status:'MATCHED',original_pages:[18],compare_pages:[19],alignment_score:.99},{status:'INSERTED',original_pages:[],compare_pages:[17],alignment_score:1}];
  it('uses matched pages after insertions instead of the same page number',()=>{expect(counterpart(entries,18,'original')).toBe(19);expect(counterpart(entries,19,'compare')).toBe(18)});
  it('does not jump the opposite document for inserted or unknown pages',()=>{expect(counterpart(entries,17,'compare')).toBeNull();expect(counterpart(entries,2,'compare')).toBeNull()});
  it('does not sync an uncertain alignment',()=>expect(counterpart([{status:'UNCERTAIN',original_pages:[1],compare_pages:[2],alignment_score:.3}],1,'original')).toBeNull());
  it('preserves explicit merged and split correspondence in both directions',()=>{
    const merged=[{status:'MERGED',original_pages:[5,6],compare_pages:[8],alignment_score:.9}];
    expect(counterpart(merged,6,'original')).toBe(8);expect(counterpart(merged,8,'compare')).toBe(5);
    const split=[{status:'SPLIT',original_pages:[7],compare_pages:[9,10],alignment_score:.9}];
    expect(counterpart(split,7,'original')).toBe(9);expect(counterpart(split,10,'compare')).toBe(7);
  });
});
