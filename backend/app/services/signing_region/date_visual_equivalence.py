"""Compare the date's ink, independently of the OCR strings."""
from pathlib import Path


def date_ink_equivalence(original, left, compare, right) -> dict:
    result = {'method':'DATE_INK_AFFINE_BIDIRECTIONAL','equivalent':False,
              'original_element_id':left.element_id,'compare_element_id':right.element_id}
    try:
        import fitz
        import cv2
        import numpy as np

        def crop(document,element):
            if not Path(document.path).is_file():
                return None
            model_page = next(p for p in document.pages if p.page_no == element.page_no)
            with fitz.open(document.path) as pdf:
                page = pdf[element.page_no-1]
                sx,sy = page.rect.width/model_page.width,page.rect.height/model_page.height
                b = element.bbox
                value_roi = element.raw_ref.get('value_roi') or {}
                x0 = max(b.x0,float(value_roi.get('x0',b.x0)))
                clip = fitz.Rect((x0-3)*sx,(b.y0-3)*sy,(b.x1+3)*sx,(b.y1+3)*sy) & page.rect
                if clip.is_empty or clip.width < 50 or clip.height > 65:
                    return None
                pix = page.get_pixmap(matrix=fitz.Matrix(3,3),clip=clip,colorspace=fitz.csRGB,alpha=False)
                rgb = np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3)
                return rgb[:,:,0].copy()  # Suppress red stamp ink, retain black date strokes.
        a,b = crop(original,left),crop(compare,right)
        if a is None or b is None:
            return result
        sift = cv2.SIFT_create(contrastThreshold=.015)
        ka,da = sift.detectAndCompute(a,None)
        kb,db = sift.detectAndCompute(b,None)
        if da is None or db is None or min(len(da),len(db)) < 16:
            return result
        pairs = cv2.BFMatcher().knnMatch(da,db,k=2)
        unique = {}
        for pair in sorted((p for p in pairs if len(p)==2),key=lambda p:p[0].distance):
            if pair[0].distance < .72*pair[1].distance:
                unique.setdefault(pair[0].trainIdx,pair[0])
        matches = list(unique.values())
        if len(matches)<16:
            return result
        src = np.float32([ka[m.queryIdx].pt for m in matches])
        dst = np.float32([kb[m.trainIdx].pt for m in matches])
        matrix,inliers = cv2.estimateAffinePartial2D(src,dst,method=cv2.RANSAC,ransacReprojThreshold=3)
        if matrix is None or inliers is None:
            return result
        count = int(inliers.sum())
        scale = float(np.hypot(matrix[0,0],matrix[1,0]))
        spread = np.ptp(src[inliers.ravel().astype(bool)],axis=0)
        result.update(matches=len(matches),inliers=count,scale=scale,horizontal_support=float(spread[0]/a.shape[1]))
        if count<16 or count/len(matches)<.65 or not .85<=scale<=1.15 or spread[0]<a.shape[1]*.65:
            return result
        aw = cv2.warpAffine(a,matrix,(b.shape[1],b.shape[0]),borderValue=255)
        visible = cv2.warpAffine(np.ones(a.shape,np.uint8),matrix,(b.shape[1],b.shape[0]))
        # Do not let a crop hide deleted or added ink on either side.
        result['compare_clipped_ink']=int(((b<190)&(visible==0)).sum())
        if result['compare_clipped_ink']>8:
            return result
        inverse = cv2.invertAffineTransform(matrix)
        visible_a = cv2.warpAffine(np.ones(b.shape,np.uint8),inverse,(a.shape[1],a.shape[0]))
        result['original_clipped_ink']=int(((a<190)&(visible_a==0)).sum())
        if result['original_clipped_ink']>8:
            return result
        am,bm = (aw<190).astype('uint8'),(b<190).astype('uint8')
        if min(am.sum(),bm.sum())<150:
            return result
        metrics=[]
        for x,y in ((am,bm),(bm,am)):
            novel = x*(1-cv2.dilate(y,np.ones((5,5),np.uint8)))
            _,_,stats,_ = cv2.connectedComponentsWithStats(novel,8)
            # A missing digit cannot be averaged away by the unchanged year.
            largest_height = max((int(s[cv2.CC_STAT_HEIGHT]) for s in stats[1:] if s[cv2.CC_STAT_AREA]>=8),default=0)
            largest_area = max((int(s[cv2.CC_STAT_AREA]) for s in stats[1:]),default=0)
            metrics.append({'novel_ratio':float(novel.sum()/x.sum()),'largest_height':largest_height,'largest_area':largest_area})
        result['ink_metrics']=metrics
        result['equivalent']=all(m['novel_ratio']<=.06 and m['largest_height']<=b.shape[0]*.2
                                 and m['largest_area']<=50 for m in metrics)
    except (ImportError,OSError,ValueError,RuntimeError,StopIteration,IndexError):
        return result
    return result


def reconcile_date_ink(original,compare,matches):
    diagnostics=[]
    for left_region,right_region,_ in matches:
        if left_region is None or right_region is None:
            continue
        for left in left_region.elements:
            if left.raw_ref.get('field_key')!='date' or not left.text.strip():
                continue
            peers=[e for e in right_region.elements if e.raw_ref.get('field_key')=='date'
                   and e.raw_ref.get('party_role')==left.raw_ref.get('party_role') and e.text.strip()]
            if len(peers)!=1 or left.text==peers[0].text:
                continue
            right=peers[0]
            proof=date_ink_equivalence(original,left,compare,right)
            diagnostics.append(proof)
            if proof['equivalent']:
                left.raw_ref['paired_date_ink']=proof
                right.raw_ref['paired_date_ink']=proof
    return diagnostics


def remove_owned_date_table_diffs(diffs,original_regions,compare_regions):
    """Propagate proved field equality to a legacy table's date-only row."""
    import re
    from collections import Counter
    def values(text):
        parts = [re.sub(r'^(?:日期|时间)[:：]', '',p.strip()) for p in text.split('|')]
        if not parts or any(not re.fullmatch(r'[0-9年月日./\s-]+',p) for p in parts):
            return None
        return Counter(re.sub(r'\s+','',p) for p in parts)
    kept=[]
    for diff in diffs:
        if diff.source_type!='table' or diff.diff_type!='MODIFY' or not diff.original_evidence:
            kept.append(diff)
            continue
        pages={e.page_no for e in diff.original_evidence}
        left=[e for r in original_regions for e in r.elements if e.page_no in pages
              and e.raw_ref.get('paired_date_ink',{}).get('equivalent')]
        proofs=[e.raw_ref['paired_date_ink'] for e in left]
        right=[e for r in compare_regions for e in r.elements if e.raw_ref.get('paired_date_ink') in proofs]
        if (not left or not right or values(diff.original_text)!=Counter(re.sub(r'\s+','',e.text) for e in left)
                or values(diff.compare_text)!=Counter(re.sub(r'\s+','',e.text) for e in right)):
            kept.append(diff)
            continue
        def contained(e,fields):
            b=e.bbox
            area=max(1,(b.x1-b.x0)*(b.y1-b.y0))
            return any(f.page_no==e.page_no and
                       max(0,min(b.x1,f.bbox.x1)-max(b.x0,f.bbox.x0))*
                       max(0,min(b.y1,f.bbox.y1)-max(b.y0,f.bbox.y0))/area>=.98 for f in fields)
        if not all(contained(e,left) for e in diff.original_evidence) or not all(contained(e,right) for e in diff.compare_evidence):
            kept.append(diff)
            continue
        for proof in proofs:
            proof.setdefault('covered_table_diff_ids',[]).append(diff.diff_id)
    return kept
