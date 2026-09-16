"""TextIn v3 PDF-pair compatibility surface; engine and identity remain local.

Supported subset is deliberately explicit: unsupported converter/token features
are errors, never silently accepted. This router does not call TextIn.
"""

from __future__ import annotations
import base64
import binascii
import io
import json
from datetime import datetime
from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from app.api import _load_accessible_or_404, require_app_role
from app.auth.dependencies import get_current_user
from app.auth.models import CurrentUser
from app.application.compare_tasks import default_compare_task_application
from app.config import settings
from app.models import CompareOptions, CompareTask
from app.services.diff_events import event_response
from app.utils.id_utils import generate_task_id
from app.utils.file_utils import FileValidationError


class CompatibilityRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request):
            try:
                return await handler(request)
            except StarletteHTTPException as exc:
                return JSONResponse(
                    envelope(code=exc.status_code, msg=str(exc.detail)),
                    status_code=exc.status_code,
                    headers=exc.headers,
                )
            except RequestValidationError:
                return JSONResponse(envelope(code=400, msg="请求参数无效。"), status_code=400)

        return wrapped


router = APIRouter(
    prefix="/api/contracts/v3/comparison/external", tags=["textin-compatible"], route_class=CompatibilityRoute
)


class DocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    filename: str
    filedata: str


class CreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    standard_doc: list[DocumentInput] = Field(min_length=1, max_length=1)
    compare_doc: list[DocumentInput] = Field(min_length=1, max_length=1)
    convert_arg: dict = Field(default_factory=dict)
    config: dict = Field(default_factory=dict)
    creator: str = ""
    token_mode: int = 0


def envelope(data=None, *, code=200, msg="success"):
    return {"code": code, "msg": msg, "data": data}


def _options(payload: CreateInput) -> tuple[CompareOptions, bool]:
    if payload.token_mode != 0:
        raise ValueError("当前预览使用本服务登录权限，仅支持 token_mode=0。")
    defaults = {
        "merge_diff": 1,
        "remove_comments": 0,
        "remove_symbol": 0,
        "ignored_symbols": [],
    }
    for key, value in payload.convert_arg.items():
        if key not in defaults:
            raise ValueError(f"不支持 convert_arg.{key}")
        if key in ("remove_comments", "remove_symbol", "ignored_symbols") and value != defaults[key]:
            raise ValueError(f"尚不支持 convert_arg.{key} 的该取值")
        if key == "merge_diff" and (
            type(value) is not int or value not in (0, 1)
        ):
            raise ValueError(f"convert_arg.{key} 必须为 0 或 1")
    for key, value in payload.config.items():
        if key not in ("use_pdf_parser", "remove_watermark") or value not in (
            (1, True, "true") if key == "use_pdf_parser" else (0, False, "false")
        ):
            raise ValueError(f"尚不支持 config.{key} 的该取值")
    arg = {**defaults, **payload.convert_arg}
    return CompareOptions(), bool(arg["merge_diff"])


@router.post("/create")
async def create(request: Request, user: CurrentUser = Depends(require_app_role)):
    # Bound the JSON body before buffering/decoding base64. Both PDFs share the
    # existing per-file byte limit; malformed input must not create a task.
    limit = 2 * ((settings.max_upload_size_mb * 1024 * 1024 + 2) // 3 * 4) + 65536
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            return JSONResponse(envelope(code=413, msg="请求超过文件大小限制。"), status_code=413)
    uploads = []
    try:
        if request.headers.get("x-ti-app-id") or request.headers.get("x-ti-secret-code"):
            raise ValueError("当前兼容入口使用本服务 Bearer 登录认证，不接受 TextIn 密钥。")
        payload = CreateInput.model_validate_json(body)
        options, merge = _options(payload)
        for item in (payload.standard_doc[0], payload.compare_doc[0]):
            if not item.filename.lower().endswith(".pdf"):
                raise ValueError("当前仅支持单对 PDF 文档。")
            content = base64.b64decode(item.filedata, validate=True)
            if not content.startswith(b"%PDF-"):
                raise ValueError("文件内容不是 PDF。")
            if len(content) > settings.max_upload_size_mb * 1024 * 1024:
                raise ValueError("PDF 超过文件大小限制。")
            uploads.append(UploadFile(filename=item.filename, file=io.BytesIO(content)))
        task_id = generate_task_id()
        await default_compare_task_application.submit_uploads(
            task_id=task_id,
            original_file=uploads[0],
            compare_file=uploads[1],
            compare_options=options,
            owner=user,
            external_compare_options={"merge_diff": merge, "creator": payload.creator, "contract_version": "v3-pdf-v1"},
        )
        origin = str(request.base_url).rstrip("/")
        prefix = f"{origin}/api/contracts/v3/comparison/external"
        preview_base = (settings.textin_preview_base_url or origin).rstrip("/")
        return envelope(
            {
                "task_id": task_id,
                "preview_url": f"{preview_base}/tasks/{task_id}?view=events",
                "diff_info_url": f"{prefix}/diff_info?task_id={task_id}&with_diff_result=true",
                "similarity_url": f"{prefix}/similarity?task_id={task_id}",
            }
        )
    except (ValueError, ValidationError, binascii.Error, FileValidationError):
        # Do not echo pydantic input values (base64 or credentials) in errors.
        return JSONResponse(
            envelope(code=400, msg="无效或尚未支持的请求：仅支持单对 base64 PDF、token_mode=0 和已公布的转换选项。"),
            status_code=400,
        )
    finally:
        for upload in uploads:
            await upload.close()


def serialize_task(task: CompareTask, with_diff_result: bool = True) -> dict:
    status = {"COMPLETED": 1, "PROCESSING": 2, "FAILED": -1}[task.status]
    if status != 1:
        return envelope({"status": status, "metrics": {}, "result": []})
    response = event_response(task)
    result = []
    for event in response["events"]:
        d = event["diff"]
        sides = []
        for doc_index, side in enumerate(("original", "compare"), 1):
            lines = []
            for i, evidence in enumerate(d[f"{side}_evidence"]):
                b = evidence["bbox"]
                polygon = [b["x0"], b["y0"], b["x1"], b["y0"], b["x1"], b["y1"], b["x0"], b["y1"]]
                lines.append(
                    {
                        "doc_index": doc_index,
                        "page_index": evidence["page_no"] - 1,
                        "line_index": i,
                        "text": evidence["text"],
                        "polygon": polygon,
                        "char_polygons": [polygon_for(char["bbox"]) for char in evidence.get("char_boxes", [])]
                        if "".join(char["char"] for char in evidence.get("char_boxes", [])) == evidence["text"]
                        else [],
                        "sub_info": None,
                        "cell_position": "",
                        "structured_id": 0,
                        "table_index": 0,
                        "sub_type": "",
                        "merged": len(event["children"]) > 1,
                    }
                )
            sides.append(lines)
        kind = (
            "image"
            if d["source_type"] == "graphic"
            else "stamp"
            if d["source_type"] == "seal"
            else "table"
            if d["source_type"] == "table"
            else "text"
        )
        kind = event.get("element_type", kind)
        status_name = {"ADD": "INSERT", "DELETE": "DELETE", "MODIFY": "CHANGE"}[d["diff_type"]]
        entry = {
            "element_id": event["event_id"],
            "elements": json.dumps(event["raw_diff_ids"]),
            "status": status_name,
            "type": kind,
            "diff": sides,
        }
        if kind in {"table", "cell", "row", "col"}:
            cells = d.get("match_score_details", {}).get("cell_observations", [])
            nested = []
            for cell in cells:
                cell_sides = []
                for doc_index, side in enumerate(("original", "compare"), 1):
                    value = cell.get(f"{side}_cell")
                    if value is None:
                        cell_sides.append([])
                        continue
                    cell_sides.append(
                        [
                            {
                                "doc_index": doc_index,
                                "page_index": value["page_no"] - 1,
                                "line_index": 0,
                                "text": value["text"],
                                "polygon": polygon_for(value["bbox"]) if value["bbox"] else None,
                                "char_polygons": [],
                                "sub_info": None,
                                "cell_position": cell_position(value),
                                "structured_id": value["table_id"],
                                "table_index": 0,
                                "sub_type": "",
                                "merged": value["rowspan"] > 1 or value["colspan"] > 1,
                            }
                        ]
                    )
                if any(cell_sides):
                    nested.append(
                        {
                            "type": "text",
                            "status": {"ADD": "INSERT", "DELETE": "DELETE", "MODIFY": "CHANGE"}[cell["diff_type"]],
                            "diff": cell_sides,
                        }
                    )
            entry.update(
                diff=nested or [{"type": "text", "status": status_name, "diff": sides}],
                area=[line for side in sides for line in side],
                cell_cnt=len(nested),
            )
        result.append(entry)
    pages = {}
    for i, side in enumerate(("original", "compare"), 1):
        profile = task.document_profiles.get(side)
        pages[f"page_list{i}"] = (
            [
                {
                    "pi": p.page_no - 1,
                    "page_width": p.width,
                    "page_height": p.height,
                    "angle": 0,
                    "original_angle": 0,
                    "type": "pdf",
                }
                for p in profile.page_profiles
            ]
            if profile
            else []
        )
    pair = {
        "standard_doc_id": task.task_id + "-1",
        "compare_doc_id": task.task_id + "-2",
        "standard_doc_name": task.original_filename,
        "compared_doc_name": task.compare_filename,
        "create_time": int(datetime.fromisoformat(task.created_at).timestamp()),
        "diff_info": response["counts"],
        "pages": pages,
        "similarity": None,
    }
    if with_diff_result:
        pair["diff_result"] = result
    return envelope({"status": 1, "metrics": {}, "result": [pair]})


@router.get("/diff_info")
def diff_info(task_id: str, with_diff_result: bool = False, user: CurrentUser = Depends(get_current_user)):
    return serialize_task(_load_accessible_or_404(task_id, user), with_diff_result)


@router.get("/similarity")
def similarity(task_id: str, user: CurrentUser = Depends(get_current_user)):
    _load_accessible_or_404(task_id, user)
    return JSONResponse(envelope(code=501, msg="未实现 TextIn 相似度算法。"), status_code=501)


@router.post("/token/apply")
def unsupported_preview_token(user: CurrentUser = Depends(get_current_user)):
    return JSONResponse(
        envelope(code=501, msg="当前预览使用本服务登录权限，尚未实现 TextIn 预览令牌协议。"), status_code=501
    )


def polygon_for(b):
    return [b["x0"], b["y0"], b["x1"], b["y0"], b["x1"], b["y1"], b["x0"], b["y1"]]


def cell_position(cell):
    def col_label(index):
        result = ""
        index += 1
        while index:
            index, remainder = divmod(index - 1, 26)
            result = chr(65 + remainder) + result
        return result

    return f"{col_label(cell['col'])}{cell['row'] + 1}:{col_label(cell['col'] + cell['colspan'] - 1)}{cell['row'] + cell['rowspan']}"
