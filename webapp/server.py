from __future__ import annotations

import base64
import hashlib
import json
import math
import mimetypes
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

try:
    from webapp.providers.gemini import (
        GeminiHTTPError,
        generate_image as gemini_generate_image,
        generate_text as gemini_generate_text,
        get_model as gemini_get_model,
    )
    from webapp.providers.rightcode import (
        RightCodeHTTPError,
        generate_image as rightcode_generate_image,
        generate_text as rightcode_generate_text,
        list_models as rightcode_list_models,
    )
except ModuleNotFoundError:  # Support running this file directly from webapp/.
    from providers.gemini import (
        GeminiHTTPError,
        generate_image as gemini_generate_image,
        generate_text as gemini_generate_text,
        get_model as gemini_get_model,
    )
    from providers.rightcode import (
        RightCodeHTTPError,
        generate_image as rightcode_generate_image,
        generate_text as rightcode_generate_text,
        list_models as rightcode_list_models,
    )


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".webapp"
JOBS_DIR = STATE_DIR / "jobs"
CONFIG_PATH = STATE_DIR / "config.json"
VOICE_CACHE_PATH = STATE_DIR / "minimax_voice_cache.json"
PREFERENCES_PATH = STATE_DIR / "preferences.json"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
NODE = shutil.which("node") or "node"
REMOTION_RENDERER = ROOT / "video_renderer"
REMOTION_MEDIA_DIR = REMOTION_RENDERER / "node_modules" / "@remotion" / "compositor-win32-x64-msvc"
FFMPEG = shutil.which("ffmpeg") or str(REMOTION_MEDIA_DIR / "ffmpeg.exe")
FFPROBE = shutil.which("ffprobe") or str(REMOTION_MEDIA_DIR / "ffprobe.exe")
HAND = ROOT / "assets" / "drawing-hand-clean.png"
PIPELINE_VERSION = "narrated_deck_v11_memory_remotion"
ALIGNMENT_SEGMENTATION = "word-boundary-dtw-audio-v2"
VIDEO_RESULT_FILES = {
    "final.mp4",
    "final-remotion-v1.mp4",
    "final-memory-remotion-v1.mp4",
}

DEFAULT_CONFIG = {
    "ai_provider": "openlux",
    "api_key": "",
    "base_url": "https://api.openlux.ai/v1",
    "text_model": "gpt-5",
    "image_model": "gpt-image-2",
    "gemini_api_key": "",
    "gemini_text_model": "gemini-3.7-flash",
    "gemini_image_model": "gemini-3.1-flash-image",
    "rightcode_api_key": "",
    "rightcode_text_base_url": "https://www.rightapi.ai/codex/v1",
    "rightcode_image_base_url": "https://www.rightapi.ai/draw/v1",
    "rightcode_task_base_url": "https://www.rightapi.ai/v1/tasks",
    "rightcode_text_model": "gpt-5.5",
    "rightcode_image_model": "gpt-image-2",
    "minimax_api_key": "",
    "minimax_base_url": "https://api.minimaxi.com",
    "minimax_api_type": "t2a_v2",
    "minimax_speech_model": "speech-2.8-hd",
    "minimax_clone_prefix": "csboard",
}

DEFAULT_STYLE = "极简粗线简笔白板风"
INFOGRAPHIC_STYLE = "国风动态信息图"
MEMORY_HANDDRAW_STYLE = "岁月回忆手绘风"
MEMORY_HANDDRAW_PIPELINE_ENABLED = True
STYLE_PRESETS = {
    INFOGRAPHIC_STYLE: (
        "暖米白宣纸背景，深灰正文与朱红重点，低饱和靛青辅助色；"
        "固定总标题和章节标题，以知识卡片、关系线、时间轴、层级或对比结构组织观点，"
        "搭配克制的国风淡彩插画，大量留白，成人知识内容，禁止摄影写实和儿童卡通。"
    ),
    MEMORY_HANDDRAW_STYLE: (
        "温暖纸白背景，深灰黑色钢笔线稿清楚、连续且便于提取，低饱和赭石、灰蓝、豆绿与旧玫瑰色像彩铅轻铺；"
        "保留老人真实年龄、皱纹、发色和生活质感，以成人回忆录的克制镜头表现一段具体记忆。"
        "构图温暖、复古、留白充足，避免儿童卡通、高饱和、霓虹、塑料 3D、照片写实和厚重油画。"
    ),
    "极简粗线简笔白板风": (
        "暖白色纯净背景，圆润有亲和力的粗黑马克笔轮廓，人物和物体高度概括，"
        "只使用橙色与钴蓝色做少量平涂点缀；几乎没有阴影、纹理和细碎结构，留白充足，"
        "像现场快速画出的清爽白板简笔画。"
    ),
    "极简商务涂鸦风": (
        "冷白至极浅灰背景，深海军蓝的精准几何轮廓，钴蓝与青绿色作为强调色；"
        "用整齐的卡片、流程箭头、图表和图标组织信息，线条克制利落、间距规整，"
        "呈现专业的商业演示和科技产品解说感，禁止暖黄纸张与随意手绘笔触。"
    ),
    "暖米黄素描白板风": (
        "温暖米黄色纸张底色，真实石墨铅笔线条，轻柔排线、交叉线和深浅笔压，"
        "辅以低饱和赭石色与灰蓝色；保留手工速写的纸张颗粒和结构细节，"
        "像一本质感细腻的编辑手账，不能画成粗线扁平图标。"
    ),
    "粗线扁平国风卡通": (
        "温暖宣纸色背景，深棕色粗轮廓，朱红、玉绿与靛青的饱和平涂色块；"
        "人物比例生动简化，少量使用祥云、笔触和中式构图节奏，"
        "形成现代国风科普动画效果，禁止写实素描和欧美商务信息图观感。"
    ),
    "爆款高热吸睛风": (
        "明亮黄色高能背景，超粗黑色外轮廓，热烈橙红与电光钴蓝的大色块，"
        "夸张但友好的人物表情和动作，配合放射爆炸形、速度线与强烈斜向构图；"
        "主体要大、对比要强、第一眼就能看懂，具有热门短视频封面般的冲击力，"
        "但保持轮廓干净，不能堆满琐碎元素。"
    ),
    "黑金科技发布会风": (
        "深黑与炭灰背景，金属金色作为主轮廓和高光，少量电光青色点缀；"
        "使用精致的环形界面、几何数据结构和舞台式光影，主体高级、权威、科技感强，"
        "像高端科技产品发布会，禁止暖白纸张和可爱手绘效果。"
    ),
    "清新治愈手账风": (
        "奶油白纸张背景，圆润轻柔的手绘线条，鼠尾草绿、蜜桃粉、奶油黄和天蓝色的低饱和水彩；"
        "少量加入胶带、贴纸与植物点缀，整体通透、温暖、治愈、生活化，"
        "保持留白，禁止强烈黑线和高对比商务图表。"
    ),
    "复古报纸拼贴风": (
        "暖灰新闻纸底色，黑色油墨主体、复古红色强调块、半色调网点、丝网印刷颗粒与撕纸边缘；"
        "人物和物体像剪下后重新拼贴的编辑视觉，层次大胆、粗粝、有文化杂志感，"
        "禁止光滑渐变和现代扁平信息图。"
    ),
    "纸感隐喻拼贴风": (
        "暖米白手工纸背景，清晰纸纤维、撕边、轻微褶皱与手工裁切痕迹；人物和物体由剪纸拼贴叠层构成，"
        "带柔和浅浮雕投影，成人卡通比例、圆白眼与小黑瞳、细线鼻口。主色仅使用米杏、炭黑、深灰、暖灰、"
        "珊瑚红和灰粉，金黄只用于希望、价值或关键转折。每张图只选择定义、流程、对比、层级、因果、清单、"
        "时间或矩阵中的一个主结构，用单一具体隐喻表达观点；留白占 25%–45%，主视觉不超过 3 组，辅助符号不超过 5 类。"
        "禁止摄影写实、光滑塑料 3D、扁平矢量图标、儿童贴纸、霓虹科技 UI、文字、Logo、水印和图标堆砌。"
    ),
    "漫画墨线解释风": (
        "暖灰米白纸张背景，使用自信、粗细有变化的黑色漫画墨线；灰面和阴影只用经典圆点半色调，不用柔和渐变。"
        "黑白灰为主体，固定暖黄色只用于边牧或关键物件，每张图最多再使用两种低饱和语义色：蓝色表示输入或内容，"
        "橙色表示行动、警告或成本，紫色表示过程，绿色表示成功或完成。关系必须用具体物件、路径、状态变化和重复材料证明，"
        "不能靠装饰图标凑数。原文需要通用角色时才使用戴细圆框眼镜的圆头极简线人，胖胖的暖黄边牧只作合适的陪伴角色；"
        "抽象机制页优先画物件和状态，不强塞人物。禁止 3D、摄影写实、光滑渐变、通用卡片网格、仪表盘、杂乱装饰、Logo 和水印。"
    ),
    "3D黏土趣味风": (
        "可爱的三维黏土动画场景，圆润玩具化比例，可见细微手作指纹，"
        "珊瑚橙、青绿色、亮黄色和奶油色的柔和配色，温暖棚拍光与轻柔投影，"
        "像精致的定格动画小剧场，主体清楚，禁止二维线稿和写实摄影材质。"
    ),
    "赛博霓虹漫画风": (
        "深靛蓝至黑色背景，青色与洋红色霓虹边缘光，紫色渐变和粗黑漫画轮廓；"
        "加入克制的速度线、全息几何形与未来创作者工作室氛围，构图动感、戏剧性强，"
        "同时确保人物面部和关键物体清楚可读。"
    ),
}


def style_recipe(style: str) -> str:
    if style not in STYLE_PRESETS:
        raise RuntimeError(f"后台未加载画面风格：{style}，请重启后台后重新提交任务")
    return STYLE_PRESETS[style]


def is_infographic_job(job_id: str) -> bool:
    item = JOBS.get(job_id, {})
    return (
        item.get("reference_mode") == "infographic"
        or item.get("job_type") == "infographic"
        or item.get("style") == INFOGRAPHIC_STYLE  # Compatibility with the first preview build.
    )


PAPER_METAPHOR_STYLE = "纸感隐喻拼贴风"
PAPER_METAPHOR_REFERENCE_DIR = ROOT / "assets" / "style-references" / "paper-metaphor"
PAPER_METAPHOR_ROUTES: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ("流程", ("流程", "系统", "自动化", "生产", "步骤", "机器", "效率"), ("03-process-machine.png",)),
    ("对比", ("对比", "选择", "判断", "黑白", "两种", "不是", "而是"), ("05-choice-black-white.png", "09-road-between-extremes.png")),
    ("因果", ("原因", "结果", "影响", "关系", "伤害", "希望", "改变"), ("01-cause-heart-vs-wound.png",)),
    ("层级", ("层级", "成长", "方向", "阶段", "进阶", "山峰"), ("09-road-between-extremes.png",)),
    ("清单", ("清单", "资源", "经验", "多个", "几件", "要素"), ("08-dual-boxes.png",)),
    ("矩阵", ("矩阵", "四象限", "双维度"), ("02-balance-many-forces.png",)),
    ("对比", ("价值", "权衡", "平衡", "责任", "收益"), ("07-scale-values.png", "02-balance-many-forces.png")),
    ("因果", ("压力", "过载", "诱惑", "信息", "职场", "家庭"), ("04-overload-pushback.png", "06-work-stress.png")),
    ("对比", ("边界", "群体", "立场", "冲突", "夹击"), ("10-boundary-two-crowds.png",)),
]


def paper_metaphor_reference_context(scenes: list[dict[str, Any]]) -> tuple[list[Path], str]:
    text = " ".join(
        str(scene.get(key, ""))
        for scene in scenes
        for key in ("title", "concept", "text", "key_text", "metaphor")
    )
    structure = "定义"
    filenames = ("01-cause-heart-vs-wound.png",)
    for candidate, keywords, routed_files in PAPER_METAPHOR_ROUTES:
        if any(keyword in text for keyword in keywords):
            structure, filenames = candidate, routed_files
            break
    paths = [PAPER_METAPHOR_REFERENCE_DIR / filename for filename in filenames]
    paths = [path for path in paths if valid_image_file(path)][:3]
    if not paths:
        raise RuntimeError("纸感隐喻拼贴风的本地参考图缺失")
    instruction = (
        f"这些输入图仅作为纸艺视觉语言与“{structure}”构图参考，不提供人物身份或具体故事。"
        "只迁移纸纤维、撕边、叠层阴影、配色、构图密度与情绪表达；禁止照搬参考图中的人物、商品、文字、符号和场景组合。"
        f"本图统一使用“{structure}”作为唯一主结构，先用一个具体主隐喻表达观点，不做逐句图标化。"
    )
    return paths, instruction


OIL_VISUAL_STYLE = "漫画墨线解释风"
OIL_VISUAL_REFERENCE_DIR = ROOT / "assets" / "style-references" / "oil-visual"


def oil_visual_reference_context(scenes: list[dict[str, Any]], infographic: bool = False) -> tuple[list[Path], str]:
    scene = scenes[0] if scenes else {}
    layout_type = str(scene.get("layout_type") or scene.get("visual_structure") or "focus")
    text = " ".join(
        str(item.get(key, ""))
        for item in scenes
        for key in ("title", "concept", "text", "key_text", "visual_strategy", "illustration_elements")
    )
    if layout_type == "comparison" or any(word in text for word in ("对比", "差异", "两种", "成本", "取舍")):
        visual_mode, filename = "对比关系", "explainer-cost-comparison.png"
    elif layout_type == "cycle" or any(word in text for word in ("循环", "反馈", "闭环")):
        visual_mode, filename = "机制循环", "feedback-loop.png"
    elif layout_type in {"path", "flow", "cause", "timeline"} or any(word in text for word in ("机制", "流程", "步骤", "瓶颈", "管线")):
        visual_mode, filename = "机制流程", "pipeline-bottleneck.png"
    elif any(word in text for word in ("人物", "角色", "讲解者", "陪伴", "团队", "主人公")):
        visual_mode, filename = "角色场景", "transparent-illustration.png"
    else:
        visual_mode, filename = "概念解释", "from-complex-to-clear.png"
    path = OIL_VISUAL_REFERENCE_DIR / filename
    if not valid_image_file(path):
        raise RuntimeError("漫画墨线解释风的本地参考图缺失")
    division = (
        "Remotion 已负责中文标题、标签、线条和关系结构，本图只生成插画证据。"
        if infographic else
        "程序会另行添加中文重点文字，本图只生成视觉证据。"
    )
    instruction = (
        f"输入图仅作为漫画墨线视觉语言与“{visual_mode}”表达方式的参考，不提供本页文字或具体故事。"
        "只迁移粗细墨线、圆点半色调、暖灰纸张、克制语义色和极简角色比例；"
        "严禁复制参考图中的英文、标签、箭头、流程线、界面、Logo、原场景组合和原观点。"
        f"{division}"
    )
    return [path], instruction

app = FastAPI(title="白板声画工坊", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:13000", "http://127.0.0.1:13000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS: dict[str, dict[str, Any]] = {}
LOCK = threading.Lock()
VOICE_QUEUE: queue.Queue[tuple[Any, ...]] = queue.Queue()
MODEL_QUEUE: queue.Queue[tuple[Any, ...]] = queue.Queue()
WORKER_LOCK = threading.Lock()
VOICE_WORKER_THREADS: dict[int, threading.Thread] = {}
VOICE_NODE_JOBS: dict[int, str | None] = {}
VOICE_NODE_LOCK = threading.Lock()
VOICE_CACHE_LOCK = threading.Lock()
MODEL_WORKER_THREADS: list[threading.Thread] = []
RENDER_THREADS: set[threading.Thread] = set()
RENDER_THREADS_LOCK = threading.Lock()
RUNNING_PROCESSES: dict[str, subprocess.Popen[str]] = {}
RUNNING_PROCESSES_LOCK = threading.Lock()
MODEL_CONCURRENCY = 4
MAX_ACTIVE_AND_QUEUED = 20


class JobCancelled(RuntimeError):
    """Cooperative stop signal for a task cancelled from the UI."""


def is_job_cancelled(job_id: str) -> bool:
    with LOCK:
        return JOBS.get(job_id, {}).get("status") == "cancelled"


def ensure_job_active(job_id: str) -> None:
    if is_job_cancelled(job_id):
        raise JobCancelled("任务已取消")


def terminate_running_process(job_id: str) -> None:
    with RUNNING_PROCESSES_LOCK:
        process = RUNNING_PROCESSES.get(job_id)
    if process is None or process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        else:
            process.terminate()
    except (OSError, subprocess.SubprocessError):
        try:
            process.kill()
        except OSError:
            pass


def _persist_job_locked(job_id: str) -> None:
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    target = job_dir / "job.json"
    temporary = job_dir / "job.json.tmp"
    temporary.write_text(json.dumps(JOBS[job_id], ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def atomic_write_json(target: Path, value: Any) -> None:
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def valid_image_file(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 1024:
        return False
    try:
        from PIL import Image
        with Image.open(path) as image:
            image.verify()
        return True
    except Exception:
        return False


def aligned_image_pair(color_image: Path, line_image: Path) -> bool:
    """Return whether two valid images have exactly the same pixel dimensions."""
    if not valid_image_file(color_image) or not valid_image_file(line_image):
        return False
    try:
        from PIL import Image
        with Image.open(color_image) as color, Image.open(line_image) as line:
            return color.size == line.size
    except Exception:
        return False


def ensure_memory_lineart_asset(style: str, color_image: Path, line_image: Path) -> Path | None:
    """Create the line-art pair only for the memory template."""
    if style != MEMORY_HANDDRAW_STYLE:
        return None
    from scripts.make_lineart import extract_lineart
    extract_lineart(color_image, line_image)
    if not aligned_image_pair(color_image, line_image):
        line_image.unlink(missing_ok=True)
        raise RuntimeError("岁月回忆手绘风线稿与彩色母图未能保持像素对齐")
    return line_image


def valid_media_file(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 1024:
        return False
    try:
        return probe_duration(path) > 0.1
    except Exception:
        return False


def valid_timed_video(path: Path, expected_ms: int, tolerance_seconds: float = 0.22) -> bool:
    """Reject stale scene clips whose old renderer added time past the narration."""
    if not valid_media_file(path):
        return False
    try:
        return abs(probe_duration(path) - expected_ms / 1000.0) <= tolerance_seconds
    except Exception:
        return False


def fit_scene_durations(scenes: list[dict[str, Any]], audio_duration: float) -> bool:
    """Make scene timing add up exactly to the voice track without starving the final image."""
    if not scenes:
        return False
    target_ms = max(len(scenes), round(max(0.001, audio_duration) * 1000))
    minimum_ms = min(1000, target_ms // len(scenes))
    remaining_ms = target_ms - minimum_ms * len(scenes)
    weights = [max(1, len(str(scene.get("text", "")))) for scene in scenes]
    total_weight = sum(weights)
    exact_extras = [remaining_ms * weight / total_weight for weight in weights]
    extras = [int(value) for value in exact_extras]
    leftover = remaining_ms - sum(extras)
    order = sorted(range(len(scenes)), key=lambda i: exact_extras[i] - extras[i], reverse=True)
    for index in order[:leftover]:
        extras[index] += 1
    durations = [minimum_ms + extra for extra in extras]
    changed = any(int(scene.get("duration_ms", 0)) != durations[i] for i, scene in enumerate(scenes))
    for scene, duration_ms in zip(scenes, durations):
        scene["duration_ms"] = duration_ms
    return changed


def load_config() -> dict[str, Any]:
    STATE_DIR.mkdir(exist_ok=True)
    if not CONFIG_PATH.exists():
        return DEFAULT_CONFIG.copy()
    data = DEFAULT_CONFIG.copy()
    stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    # Do not migrate the former Volcengine credential into OpenLux. A key must
    # only ever be sent to the provider it was entered for.
    for key in DEFAULT_CONFIG:
        if key in stored:
            data[key] = stored[key]
    if str(data.get("text_model", "")).startswith("doubao-"):
        data["text_model"] = DEFAULT_CONFIG["text_model"]
    if str(data.get("image_model", "")).startswith("doubao-"):
        data["image_model"] = DEFAULT_CONFIG["image_model"]
    if data.get("ai_provider") not in {"openlux", "gemini", "rightcode"}:
        data["ai_provider"] = "openlux"
    return data


def safe_config(data: dict[str, Any]) -> dict[str, Any]:
    result = data.copy()
    key = result.get("api_key", "")
    result["api_key"] = "" if not key else f"{key[:4]}••••{key[-4:]}"
    result["has_api_key"] = bool(key)
    minimax_key = str(result.get("minimax_api_key", ""))
    result["minimax_api_key"] = "" if not minimax_key else "********"
    gemini_key = str(result.get("gemini_api_key", ""))
    result["gemini_api_key"] = "" if not gemini_key else "********"
    result["has_gemini_api_key"] = bool(gemini_key)
    rightcode_key = str(result.get("rightcode_api_key", ""))
    result["rightcode_api_key"] = "" if not rightcode_key else "********"
    result["has_rightcode_api_key"] = bool(rightcode_key)
    result["has_minimax_api_key"] = bool(minimax_key)
    return result




def configured_voice_services(config: dict[str, Any] | None = None) -> list[str]:
    source = config or load_config()
    url = str(source.get("minimax_base_url", "")).strip().rstrip("/")
    return [url] if url else []


def minimax_headers(config: dict[str, Any]) -> dict[str, str]:
    api_key = str(config.get("minimax_api_key", "")).strip()
    if not api_key:
        raise RuntimeError("MiniMax API key is required")
    return {"Authorization": f"Bearer {api_key}"}


def minimax_json(response: httpx.Response, action: str) -> dict[str, Any]:
    if response.is_error:
        raise RuntimeError(f"MiniMax {action} failed: HTTP {response.status_code} {response.text[:300]}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"MiniMax {action} returned invalid JSON") from exc
    base_resp = payload.get("base_resp") or {}
    status_code = int(base_resp.get("status_code", -1))
    if status_code != 0:
        message = str(base_resp.get("status_msg") or "unknown error")
        raise RuntimeError(f"MiniMax {action} failed: {status_code} {message}")
    return payload


def normalized_task_name(value: Any, script: str = "", job_id: str = "") -> str:
    explicit = re.sub(r"\s+", " ", str(value or "")).strip()
    if explicit:
        return explicit[:30]
    automatic = re.sub(r"\s+", "", script.strip())[:15]
    return automatic or f"未命名任务-{job_id[-4:]}"


def request_client_ip(request: Request) -> str:
    # The API only listens on loopback and is reached through the local Vite
    # proxy. Its last forwarded address is therefore the nearest LAN client.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        candidate = forwarded.split(",")[-1].strip()
        if candidate:
            return candidate
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else "未知 IP"


def update_job(job_id: str, **values: Any) -> None:
    with LOCK:
        if JOBS[job_id].get("status") == "cancelled" and values.get("status") != "cancelled":
            return
        JOBS[job_id].update(values)
        _persist_job_locked(job_id)


def begin_phase(job_id: str, key: str, label: str, stage: str, progress: int) -> None:
    now = time.time()
    with LOCK:
        job = JOBS[job_id]
        if job.get("status") == "cancelled":
            raise JobCancelled("任务已取消")
        previous = job.get("current_phase")
        previous_started = job.get("phase_started_at")
        timings = job.setdefault("timings", {})
        if previous and previous_started:
            entry = timings.setdefault(previous, {"label": previous, "seconds": 0.0})
            entry["seconds"] = float(entry.get("seconds", 0.0)) + max(0.0, now - float(previous_started))
        timings.setdefault(key, {"label": label, "seconds": 0.0})["label"] = label
        job.update(
            status="running", stage=stage, progress=progress,
            current_phase=key, phase_started_at=now,
        )
        _persist_job_locked(job_id)


def queue_for_stage(job_id: str, queue_stage: str, stage: str, progress: int) -> None:
    """Close the active timer and move a job to the next pipeline queue."""
    now = time.time()
    with LOCK:
        job = JOBS[job_id]
        if job.get("status") == "cancelled":
            raise JobCancelled("任务已取消")
        current = job.get("current_phase")
        started = job.get("phase_started_at")
        if current and started:
            entry = job.setdefault("timings", {}).setdefault(current, {"label": current, "seconds": 0.0})
            entry["seconds"] = float(entry.get("seconds", 0.0)) + max(0.0, now - float(started))
        job.update(
            status="queued", stage=stage, progress=progress,
            queue_stage=queue_stage, queue_order=time.time_ns(),
            current_phase=None, phase_started_at=None,
        )
        _persist_job_locked(job_id)


def finish_timing(job_id: str) -> None:
    now = time.time()
    with LOCK:
        job = JOBS[job_id]
        current = job.get("current_phase")
        started = job.get("phase_started_at")
        if current and started:
            entry = job.setdefault("timings", {}).setdefault(current, {"label": current, "seconds": 0.0})
            entry["seconds"] = float(entry.get("seconds", 0.0)) + max(0.0, now - float(started))
        job["current_phase"] = None
        job["phase_started_at"] = None
        job["finished_at"] = now
        job["total_elapsed"] = max(0.0, now - float(job.get("started_at", now)))
        _persist_job_locked(job_id)


def restore_jobs() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK:
        for metadata in JOBS_DIR.glob("*/job.json"):
            try:
                item = json.loads(metadata.read_text(encoding="utf-8"))
                job_id = str(item.get("id") or metadata.parent.name)
                item["task_name"] = normalized_task_name(item.get("task_name"), str(item.get("copy", "")), job_id)
                if item.get("status") in {"queued", "running"}:
                    current = item.get("current_phase")
                    started = item.get("phase_started_at")
                    if current and started:
                        entry = item.setdefault("timings", {}).setdefault(current, {"label": current, "seconds": 0.0})
                        entry["seconds"] = float(entry.get("seconds", 0.0)) + max(0.0, time.time() - float(started))
                    item.update(
                        status="queued", stage="服务已恢复，正在检查任务断点", error=None,
                        current_phase=None, phase_started_at=None, finished_at=None,
                        resume_count=int(item.get("resume_count", 0)) + 1,
                    )
                JOBS[job_id] = item
                _persist_job_locked(job_id)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue


def job_snapshot(job_id: str) -> dict[str, Any]:
    now = time.time()
    with LOCK:
        source = JOBS[job_id]
        result = source.copy()
        result["task_name"] = normalized_task_name(source.get("task_name"), str(source.get("copy", "")), job_id)
        result["can_retry"] = source.get("status") == "error"
        result["can_cancel"] = source.get("status") in {"queued", "running"}
        result["image_count"] = sum(
            1
            for path in (JOBS_DIR / job_id).glob("board-*.png")
            if re.fullmatch(r"board-\d+\.png", path.name)
        )
        if source.get("style") == MEMORY_HANDDRAW_STYLE:
            result["lineart_count"] = sum(
                1
                for path in (JOBS_DIR / job_id).glob("board-*.line.png")
                if re.fullmatch(r"board-\d+\.line\.png", path.name)
            )
        if (
            source.get("status") == "done"
            and result["image_count"]
            and (JOBS_DIR / job_id / "voice.wav").is_file()
            and (JOBS_DIR / job_id / "plan.json").is_file()
        ):
            result["can_rerender"] = True
        result.pop("copy", None)
        result.pop("visual_references", None)
        timings = {key: value.copy() for key, value in source.get("timings", {}).items()}
        current = source.get("current_phase")
        phase_started = source.get("phase_started_at")
        if current and phase_started and current in timings:
            timings[current]["seconds"] = float(timings[current].get("seconds", 0.0)) + max(0.0, now - float(phase_started))
            timings[current]["running"] = True
            result["current_elapsed"] = max(0.0, now - float(phase_started))
        else:
            result["current_elapsed"] = 0.0
        result["timings"] = timings
        end = source.get("finished_at") or now
        result["total_elapsed"] = max(0.0, float(end) - float(source.get("started_at", end)))
        if source.get("status") == "queued":
            order = int(source.get("queue_order", 0))
            queue_stage = str(source.get("queue_stage", "voice"))
            ahead = sum(
                1 for other_id, other in JOBS.items()
                if other_id != job_id
                and other.get("status") in {"queued", "running"}
                and str(other.get("queue_stage", "voice")) == queue_stage
                and int(other.get("queue_order", 0)) < order
            )
            result["queue_ahead"] = ahead
            queue_labels = {"voice": "语音克隆", "model": "模型调用", "render": "本地渲染"}
            label = queue_labels.get(queue_stage, "任务")
            if queue_stage == "render":
                result["stage"] = "正在进入本地渲染"
            else:
                result["stage"] = f"{label}排队中，前方 {ahead} 个任务" if ahead else f"即将开始{label}"
        return result


def run(cmd: list[str], cwd: Path = ROOT, job_id: str | None = None) -> None:
    if job_id:
        ensure_job_active(job_id)
    popen_options: dict[str, Any] = {
        "cwd": cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True
    process = subprocess.Popen(cmd, **popen_options)
    if job_id:
        with RUNNING_PROCESSES_LOCK:
            RUNNING_PROCESSES[job_id] = process
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                if job_id and is_job_cancelled(job_id):
                    terminate_running_process(job_id)
                    try:
                        process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                    raise JobCancelled("任务已取消")
        if process.returncode:
            raise RuntimeError((stderr or stdout)[-3000:])
        if job_id:
            ensure_job_active(job_id)
    finally:
        if job_id:
            with RUNNING_PROCESSES_LOCK:
                if RUNNING_PROCESSES.get(job_id) is process:
                    RUNNING_PROCESSES.pop(job_id, None)


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def extract_response_text(payload: dict[str, Any]) -> str:
    if payload.get("output_text"):
        return payload["output_text"]
    pieces = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"}:
                pieces.append(content.get("text", ""))
    for choice in payload.get("choices", []):
        message = choice.get("message", {})
        content = message.get("content", choice.get("text", ""))
        if isinstance(content, str):
            pieces.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in {"text", "output_text"}:
                    pieces.append(str(part.get("text", "")))
    return "\n".join(pieces)


def parse_json_block(text: str) -> Any:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    start = min([p for p in (text.find("["), text.find("{")) if p >= 0], default=0)
    end = max(text.rfind("]"), text.rfind("}"))
    return json.loads(text[start : end + 1])


class ProviderHTTPError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def provider_retry_delay(attempt: int) -> int:
    return (3, 8, 15)[min(attempt, 2)]


def provider_post(config: dict[str, Any], endpoint: str, payload: dict[str, Any], timeout: float = 1800, job_id: str | None = None) -> dict[str, Any]:
    if not config.get("api_key"):
        raise RuntimeError("请先在 API 设置中填写 OpenLux API Key")
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    f"{config['base_url'].rstrip('/')}/{endpoint.lstrip('/')}",
                    headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
                    json=payload,
                )
            if response.is_error:
                error = ProviderHTTPError(response.status_code, f"OpenLux 调用失败：{response.status_code} {response.text[:800]}")
                if response.status_code not in {408, 409, 425, 429, 500, 502, 503, 504}:
                    raise error
                raise error
            return response.json()
        except (httpx.TimeoutException, httpx.TransportError, ProviderHTTPError) as exc:
            last_error = exc
            retryable = not isinstance(exc, ProviderHTTPError) or exc.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
            if not retryable or attempt == 2:
                raise
            if job_id and job_id in JOBS:
                update_job(job_id, stage=f"模型服务暂时异常，正在自动重试 {attempt + 2}/3", model_retry_count=int(JOBS[job_id].get("model_retry_count", 0)) + 1)
            time.sleep(provider_retry_delay(attempt))
    raise RuntimeError(f"模型服务重试失败：{last_error}")


def provider_models(config: dict[str, Any], timeout: float = 30) -> set[str]:
    if not config.get("api_key"):
        raise RuntimeError("请先在 API 设置中填写 OpenLux API Key")
    with httpx.Client(timeout=timeout) as client:
        response = client.get(
            f"{config['base_url'].rstrip('/')}/models",
            headers={"Authorization": f"Bearer {config['api_key']}"},
        )
        if response.is_error:
            raise ProviderHTTPError(response.status_code, f"OpenLux 模型列表读取失败：{response.status_code} {response.text[:800]}")
        payload = response.json()
    return {str(item.get("id")) for item in payload.get("data", []) if isinstance(item, dict) and item.get("id")}

def selected_ai_provider(config: dict[str, Any]) -> str:
    provider = str(config.get("ai_provider", "openlux")).strip().lower()
    return provider if provider in {"openlux", "gemini", "rightcode"} else "openlux"


def provider_job_snapshot(config: dict[str, Any]) -> dict[str, str]:
    provider = selected_ai_provider(config)
    model_prefix = "gemini" if provider == "gemini" else "rightcode" if provider == "rightcode" else ""
    return {
        "ai_provider": provider,
        "provider_text_model": str(config.get(f"{model_prefix}_text_model" if model_prefix else "text_model", "")),
        "provider_image_model": str(config.get(f"{model_prefix}_image_model" if model_prefix else "image_model", "")),
    }


def model_config_for_job(job_id: str) -> dict[str, Any]:
    config = load_config()
    with LOCK:
        job = JOBS.get(job_id, {}).copy()
    provider = str(job.get("ai_provider") or "")
    if provider not in {"openlux", "gemini", "rightcode"}:
        return config
    config["ai_provider"] = provider
    if provider == "gemini":
        config["gemini_text_model"] = str(job.get("provider_text_model") or config["gemini_text_model"])
        config["gemini_image_model"] = str(job.get("provider_image_model") or config["gemini_image_model"])
    elif provider == "rightcode":
        config["rightcode_text_model"] = str(job.get("provider_text_model") or config["rightcode_text_model"])
        config["rightcode_image_model"] = str(job.get("provider_image_model") or config["rightcode_image_model"])
    else:
        config["text_model"] = str(job.get("provider_text_model") or config["text_model"])
        config["image_model"] = str(job.get("provider_image_model") or config["image_model"])
    return config


def scene_plan_schema(infographic: bool, item_limit: int) -> dict[str, Any]:
    if infographic:
        item_schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "source_phrase_ids": {"type": "array", "items": {"type": "integer"}, "minItems": 1},
                "series_title": {"type": "string"},
                "chapter_title": {"type": "string"},
                "core_idea": {"type": "string"},
                "page_title": {"type": "string"},
                "page_title_trigger_phrase_id": {"type": "integer"},
                "key_text": {"type": "string"},
                "role": {"type": "string", "enum": ["overview", "detail", "transition", "summary"]},
                "layout_type": {"type": "string"},
                "composition": {"type": "string"},
                "relationship_type": {"type": "string", "enum": ["none", "sequence", "cause"]},
                "key_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "trigger_phrase_id": {"type": "integer"},
                        },
                        "required": ["label", "trigger_phrase_id"],
                        "additionalProperties": False,
                    },
                },
                "conclusion": {"type": "string"},
                "conclusion_trigger_phrase_id": {"type": "integer"},
                "visual_strategy": {"type": "string"},
                "narrative_link": {"type": "string"},
                "illustration_elements": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
                "illustration_trigger_phrase_id": {"type": "integer"},
            },
            "required": [
                "source_phrase_ids",
                "series_title",
                "chapter_title",
                "core_idea",
                "page_title",
                "page_title_trigger_phrase_id",
                "key_text",
                "role",
                "layout_type",
                "composition",
                "relationship_type",
                "key_items",
                "conclusion",
                "conclusion_trigger_phrase_id",
                "visual_strategy",
                "narrative_link",
                "illustration_elements",
                "illustration_trigger_phrase_id",
            ],
            "additionalProperties": False,
        }
        return {"type": "array", "items": item_schema, "minItems": 1, "maxItems": max(1, item_limit)}
    item_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "key_text": {"type": "string"},
            "concept": {"type": "string"},
            "elements": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3},
            "visual_structure": {"type": "string"},
            "metaphor": {"type": "string"},
        },
        "required": ["title", "key_text", "concept", "elements"],
        "additionalProperties": False,
    }
    exact_count = max(1, item_limit)
    return {"type": "array", "items": item_schema, "minItems": exact_count, "maxItems": exact_count}


def provider_plan_text(
    config: dict[str, Any],
    prompt: str,
    *,
    infographic: bool,
    item_limit: int,
    job_id: str | None = None,
) -> str:
    provider = selected_ai_provider(config)
    if provider == "openlux":
        payload = provider_post(config, "responses", {"model": config["text_model"], "input": prompt}, job_id=job_id)
        return extract_response_text(payload)
    if provider == "rightcode":
        def on_rightcode_retry(next_attempt: int) -> None:
            if job_id and job_id in JOBS:
                ensure_job_active(job_id)
                update_job(
                    job_id,
                    stage=f"Right Code 模型服务暂时异常，正在自动重试 {next_attempt}/3",
                    model_retry_count=int(JOBS[job_id].get("model_retry_count", 0)) + 1,
                )

        try:
            return rightcode_generate_text(
                str(config.get("rightcode_api_key", "")),
                str(config.get("rightcode_text_base_url", "")),
                str(config.get("rightcode_text_model", "")),
                prompt,
                retry_callback=on_rightcode_retry,
            )
        except RightCodeHTTPError as exc:
            raise ProviderHTTPError(exc.status_code, str(exc)) from exc

    def on_retry(next_attempt: int) -> None:
        if job_id and job_id in JOBS:
            ensure_job_active(job_id)
            update_job(
                job_id,
                stage=f"Gemini 模型服务暂时异常，正在自动重试 {next_attempt}/3",
                model_retry_count=int(JOBS[job_id].get("model_retry_count", 0)) + 1,
            )

    try:
        return gemini_generate_text(
            str(config.get("gemini_api_key", "")),
            str(config.get("gemini_text_model", "")),
            prompt,
            scene_plan_schema(infographic, item_limit),
            retry_callback=on_retry,
        )
    except GeminiHTTPError as exc:
        raise ProviderHTTPError(exc.status_code, str(exc)) from exc

def script_units(copy: str) -> list[str]:
    """Use the writer's sentence and paragraph boundaries as semantic units."""
    return [x.strip() for x in re.findall(r"[^。！？!?；;\n]+[。！？!?；;]?", copy) if x.strip()]


def normalized_semantic_text(value: str) -> str:
    return "".join(character.lower() for character in str(value) if character.isalnum())


def validate_infographic_cues(pages: list[dict[str, Any]], units: list[str]) -> None:
    """Require every semantic element to have an ordered, exact source anchor."""
    for page_index, page in enumerate(pages, 1):
        indexes = [int(value) for value in page.get("source_units") or []]
        page_source = normalized_semantic_text("".join(units[index - 1] for index in indexes))
        nodes = page.get("nodes")
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 5:
            raise RuntimeError(f"第 {page_index} 页 nodes 必须包含 1～5 项")
        if not all(isinstance(node, str) and node.strip() for node in nodes):
            raise RuntimeError(f"第 {page_index} 页 nodes 必须全部是短文字")
        required_ids = {"page-title", "illustration", *(f"node-{index + 1}" for index in range(len(nodes)))}
        if str(page.get("conclusion") or "").strip():
            required_ids.add("conclusion")
        cues = page.get("cues")
        if not isinstance(cues, list) or not 1 <= len(cues) <= 6:
            raise RuntimeError(f"第 {page_index} 页必须包含 1～6 个语义 Cue")
        entered: list[str] = []
        cursor = 0
        for cue_index, cue in enumerate(cues, 1):
            if not isinstance(cue, dict):
                raise RuntimeError(f"第 {page_index} 页第 {cue_index} 个 Cue 结构无效")
            anchor = normalized_semantic_text(str(cue.get("anchor_text") or ""))
            if len(anchor) < 2:
                raise RuntimeError(f"第 {page_index} 页第 {cue_index} 个 Cue 缺少至少两个字的原文锚点")
            position = page_source.find(anchor, cursor)
            if position < 0:
                raise RuntimeError(f"第 {page_index} 页 Cue 锚点不是按顺序摘录的本页原文：{cue.get('anchor_text', '')}")
            cursor = position + len(anchor)
            enter_ids = [str(value) for value in cue.get("enter_ids") or []]
            unknown = set(enter_ids) - required_ids
            if unknown:
                raise RuntimeError(f"第 {page_index} 页 Cue 引用了未知元素：{', '.join(sorted(unknown))}")
            focus_id = str(cue.get("focus_id") or "")
            if focus_id not in required_ids:
                raise RuntimeError(f"第 {page_index} 页 Cue 的 focus_id 无效：{focus_id}")
            entered.extend(enter_ids)
        missing = required_ids - set(entered)
        duplicate = sorted({value for value in entered if entered.count(value) > 1})
        if missing:
            raise RuntimeError(f"第 {page_index} 页以下元素没有绑定 Cue：{', '.join(sorted(missing))}")
        if duplicate:
            raise RuntimeError(f"第 {page_index} 页以下元素重复绑定 Cue：{', '.join(duplicate)}")


def split_script(copy: str, target_count: int) -> list[str]:
    # Prefer complete sentences and paragraphs so a scene follows the copy.
    units = script_units(copy)
    if not units:
        return [copy]
    target_count = max(1, min(target_count, len(units)))
    groups: list[str] = []
    cursor = 0
    for group_index in range(target_count):
        remaining_groups = target_count - group_index
        remaining_units = len(units) - cursor
        if remaining_groups == 1:
            groups.append("".join(units[cursor:]).strip())
            break
        remaining_chars = sum(len(x) for x in units[cursor:])
        target_chars = remaining_chars / remaining_groups
        take = 0
        length = 0
        while take < remaining_units - (remaining_groups - 1):
            unit_len = len(units[cursor + take])
            if take and length + unit_len > target_chars * 1.18:
                break
            length += unit_len
            take += 1
            if length >= target_chars * 0.82:
                break
        take = max(1, take)
        groups.append("".join(units[cursor:cursor + take]).strip())
        cursor += take
    return groups


def scene_limit_for_duration(duration: float) -> int:
    """Duration is a ceiling only: never exceed eight scenes per minute."""
    return max(1, int(max(0.0, duration) * 8 / 60))


def numbered_section_topics(copy: str) -> list[str]:
    matches = re.finditer(r"(?:^|[“”\"'\s])([1-9])\s*[.．、]\s*([^：:\n。！？!?]{2,18})\s*[：:]", copy)
    topics: list[str] = []
    expected = 1
    for match in matches:
        number = int(match.group(1))
        if number != expected:
            continue
        topic = re.sub(r"^[\s“”\"']+|[\s“”\"']+$", "", match.group(2)).strip()
        if topic:
            topics.append(topic)
            expected += 1
    return topics if 3 <= len(topics) <= 8 else []


def _allows_directional_relation(text: str, relation_type: str) -> bool:
    if relation_type == "sequence":
        return bool(re.search(r"首先|然后|接着|随后|最后|第[一二三四五六七八九]步|步骤", text))
    if relation_type == "cause":
        return bool(re.search(r"因为|所以|导致|因此|从而|结果是|原因", text))
    return False


DECK_LAYOUTS = {
    "overview", "question", "focus", "principle", "comparison", "evidence",
    "layers", "case", "path", "flow", "cause", "cycle", "timeline", "summary",
}
DECK_COMPOSITIONS = {"split-right", "split-left", "center-stage", "top-bottom", "full-width"}


def _fallback_layout(page_text: str, role: str, relation_type: str, key_count: int, page_index: int) -> str:
    if role == "overview":
        return "overview"
    if role == "summary" or re.search(r"总结|归纳|记住|最后|总之", page_text):
        return "summary"
    if "？" in page_text or "?" in page_text:
        return "question"
    if relation_type == "comparison" or re.search(r"相比|对比|而不是|一边|另一边|彼|己", page_text):
        return "comparison"
    if relation_type == "cause":
        return "cause"
    if relation_type == "sequence":
        return "path"
    if re.search(r"案例|例如|比如|数据|证据|调查|研究", page_text):
        return "case" if page_index % 2 else "evidence"
    if re.search(r"本质|原则|核心|关键|定义|意味着", page_text):
        return "principle"
    if key_count >= 4:
        return "layers"
    return ("focus", "evidence", "case")[page_index % 3]


def _default_composition(layout_type: str, page_index: int) -> str:
    if layout_type in {"question", "principle", "cycle"}:
        return "center-stage"
    if layout_type in {"path", "flow", "cause", "timeline", "layers"}:
        return "full-width" if page_index % 2 else "top-bottom"
    if layout_type in {"comparison", "case"}:
        return "top-bottom" if page_index % 2 else "full-width"
    return "split-left" if page_index % 2 == 0 else "split-right"


def normalize_deck_pages(candidate: list[dict[str, Any]], copy: str, phrase_timeline: dict[str, Any]) -> list[dict[str, Any]]:
    phrases = phrase_timeline.get("phrases")
    if not isinstance(phrases, list) or not phrases:
        raise RuntimeError("生成 PPT 结构前必须先有完整短语时间表")
    phrase_by_id = {str(item.get("id")): item for item in phrases if isinstance(item, dict) and item.get("id")}
    all_phrase_ids = [str(item.get("id")) for item in phrases if isinstance(item, dict) and item.get("id")]
    order = {phrase_id: index for index, phrase_id in enumerate(all_phrase_ids)}
    pages: list[dict[str, Any]] = []
    used_phrase_ids: list[str] = []
    series_title = ""
    previous_layout = ""
    previous_composition = ""
    for page_index, raw in enumerate(candidate, 1):
        source_ids = [str(value) for value in raw.get("source_phrase_ids") or []]
        if not source_ids or any(value not in phrase_by_id for value in source_ids):
            raise RuntimeError(f"第 {page_index} 页缺少有效 source_phrase_ids")
        positions = [order[value] for value in source_ids]
        if positions != list(range(positions[0], positions[-1] + 1)):
            raise RuntimeError(f"第 {page_index} 页必须引用连续的短语编号")
        used_phrase_ids.extend(source_ids)
        page_text = "".join(str(phrase_by_id[value].get("text") or "") for value in source_ids)
        role = str(raw.get("role") or "detail")
        role = role if role in {"overview", "detail", "transition", "summary"} else "detail"
        layout_type = str(raw.get("layout_type") or "")
        relation_type = str(raw.get("relationship_type") or "none")
        relation_type = relation_type if relation_type in {"none", "sequence", "cause", "comparison", "hierarchy"} else "none"
        if role == "overview" or relation_type in {"sequence", "cause"} and not _allows_directional_relation(page_text, relation_type):
            relation_type = "none"

        raw_items = raw.get("key_items") or raw.get("nodes") or []
        key_items: list[dict[str, str]] = []
        for item in raw_items[:6] if isinstance(raw_items, list) else []:
            label = str(item.get("label") or item.get("text") or "") if isinstance(item, dict) else str(item)
            trigger = str(item.get("trigger_phrase_id") or source_ids[0]) if isinstance(item, dict) else source_ids[0]
            label = label.strip()[:16]
            if label:
                key_items.append({"label": label, "trigger_phrase_id": trigger})
        if not key_items:
            raise RuntimeError(f"第 {page_index} 页没有可显示的关键词")
        fallback_layout = _fallback_layout(page_text, role, relation_type, len(key_items), page_index)
        if layout_type not in DECK_LAYOUTS:
            layout_type = fallback_layout
        if layout_type == previous_layout and role == "detail" and fallback_layout != previous_layout:
            layout_type = fallback_layout
        composition = str(raw.get("composition") or "")
        if composition not in DECK_COMPOSITIONS:
            composition = _default_composition(layout_type, page_index)
        if composition == previous_composition and layout_type not in {"question", "principle", "cycle"}:
            composition = _default_composition(layout_type, page_index + 1)
        trigger_fields = {
            "page_title_trigger_phrase_id": str(raw.get("page_title_trigger_phrase_id") or source_ids[0]),
            "illustration_trigger_phrase_id": str(raw.get("illustration_trigger_phrase_id") or source_ids[0]),
            "conclusion_trigger_phrase_id": str(raw.get("conclusion_trigger_phrase_id") or source_ids[-1]),
        }
        for item in key_items:
            if item["trigger_phrase_id"] not in source_ids:
                raise RuntimeError(f"第 {page_index} 页关键词“{item['label']}”引用了本页之外的短语")
        if any(value not in source_ids for value in trigger_fields.values()):
            raise RuntimeError(f"第 {page_index} 页存在跨页元素时间绑定")

        series_title = series_title or str(raw.get("series_title") or copy[:30]).strip()[:30]
        illustration = raw.get("illustration_elements") or []
        illustration = [str(value.get("label") or "") if isinstance(value, dict) else str(value) for value in illustration]
        illustration = [value.strip()[:24] for value in illustration if value.strip()][:3]
        page = {
            "source_phrase_ids": source_ids,
            "series_title": series_title,
            "chapter_title": str(raw.get("chapter_title") or raw.get("page_title") or "本章要点").strip()[:24],
            "page_title": str(raw.get("page_title") or raw.get("primary_sentence") or "本页重点").strip()[:24],
            "key_text": str(raw.get("key_text") or raw.get("page_title") or "本页重点").strip()[:16],
            "role": role,
            "layout_type": layout_type,
            "composition": composition,
            "relationship_type": relation_type,
            "key_items": key_items,
            "nodes": [item["label"] for item in key_items],
            "conclusion": str(raw.get("conclusion") or "").strip()[:20],
            "core_idea": str(raw.get("core_idea") or raw.get("concept") or raw.get("page_title") or "").strip()[:80],
            "concept": str(raw.get("core_idea") or raw.get("concept") or raw.get("page_title") or "").strip()[:80],
            "visual_strategy": str(raw.get("visual_strategy") or "左侧文字，右侧主题插图").strip()[:80],
            "narrative_link": str(raw.get("narrative_link") or "承接本页旁白并进入下一部分").strip()[:80],
            "illustration_elements": illustration or [item["label"] for item in key_items[:2]],
            "text": page_text,
            "_plan_mode": "narrated_deck_v4",
            **trigger_fields,
        }
        pages.append(page)
        previous_layout = layout_type
        previous_composition = composition

    if used_phrase_ids != all_phrase_ids:
        raise RuntimeError("PPT 页面没有按顺序完整覆盖全部短语")

    topics = numbered_section_topics(copy)
    if topics and pages:
        first_page = pages[0]
        trigger = next(
            (
                phrase_id for phrase_id in first_page["source_phrase_ids"]
                if re.search(rf"(?:这|以下)?{len(topics)}项", str(phrase_by_id[phrase_id].get("text") or ""))
            ),
            first_page["source_phrase_ids"][-1],
        )
        first_page["role"] = "overview"
        first_page["layout_type"] = "overview"
        first_page["composition"] = "split-right"
        first_page["relationship_type"] = "none"
        first_page["key_items"] = [{"label": topic[:16], "trigger_phrase_id": trigger} for topic in topics]
        first_page["nodes"] = topics
        first_page["illustration_elements"] = ["儿童大脑侧面轮廓", "被五种训练共同激活的脑区", "柔和发光的神经连接"]
        first_page["concept"] = f"用一页总览明确列出{len(topics)}项训练，右侧用大脑插图解释整体主题"
    return pages


def make_plan(
    config: dict[str, Any],
    copy: str,
    duration: float,
    style: str,
    character_context: str = "",
    job_id: str | None = None,
    infographic: bool = False,
    phrase_timeline: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    # The copy decides how many meaningful scenes exist. Duration only caps
    # their density so short narration never receives too many images.
    source_units = script_units(copy) or [copy.strip()]
    phrase_items = phrase_timeline.get("phrases") if infographic and isinstance(phrase_timeline, dict) else None
    if infographic and (not isinstance(phrase_items, list) or not phrase_items):
        raise RuntimeError("动态 PPT 必须先完成短语与真实旁白时间对齐")
    requested_count = min(len(phrase_items) if isinstance(phrase_items, list) else len(source_units), scene_limit_for_duration(duration))
    segments = split_script(copy, requested_count)
    scene_count = len(segments)
    fixed_segments = "\n".join(f"第{i + 1}幕原文：{text}" for i, text in enumerate(segments))
    character_rule = (
        f"可用人物如下：{character_context}。根据原文语义选择出场人物，并在 title、concept 和 elements 中写明人物名称；不得改变人物身份与外观。"
        if character_context else
        "原文指定的人物或动物身份必须优先保持；没有指定身份且确实需要讲解角色时，使用戴细圆框眼镜的圆头极简线人。"
        "暖黄色边牧只在陪伴、协作或生活化角色场景中出现，抽象机制页不要强塞人物或宠物。同一角色外观保持一致。"
        if style == OIL_VISUAL_STYLE else
        "主角必须严格来自原文；原文是动物就保持该动物，原文没有指定身份时才使用普通中国青年。所有分镜中的同一角色外观保持一致。"
        if style == PAPER_METAPHOR_STYLE else
        "同一位主角始终是“中国青年男性，短黑发，朴素深色上衣”，人物外观必须保持一致。"
    )
    paper_rule = (
        "额外为每幕输出 visual_structure 和 metaphor：visual_structure 只能从定义、流程、对比、层级、因果、清单、时间、矩阵中选择一项；"
        "metaphor 只写一个可被画出的核心隐喻。不要把文案中的每个名词都转成图标。"
        if style == PAPER_METAPHOR_STYLE else ""
    )
    if infographic:
        oil_visual_rule = (
            "13. 当前风格的插图证据从四类中择一：概念解释用具体隐喻呈现从复杂到清晰；机制流程用物件、通道和状态变化；"
            "对比关系用左右两组可比对象；角色场景用动作和环境物件。每页只选最匹配的一类并写进 visual_strategy，"
            "不要把四类混在一页，也不要为了出现固定角色而改变原意。\n"
            if style == OIL_VISUAL_STYLE else ""
        )
        numbered_phrases = "\n".join(
            f"[{item['id']}｜{item['spoken_start_ms']}–{item['spoken_end_ms']}ms] {item['text']}"
            for item in phrase_items
        )
        prompt = f"""你是中文口播动态 PPT 的内容编辑。短语与真实音频时间已经在上一步确定；你只梳理页面结构，不得重新估算时间。
总口播时长约 {duration:.1f} 秒，最多 {requested_count} 页。画面风格和插图在后续步骤处理。

分页原则：
1. 一般用本页第一条短语作为中心句的依据，浓缩为 page_title；关键词只辅助中心句，不得抢成另一套观点。
2. 如果开头提到“以下N项/这N项”，且全文随后存在 1、2、3…编号章节，必须单独生成 role=overview 的总览页；key_items 必须使用后文各编号章节名称，不能改写为能力、效果或抽象概念。
3. source_phrase_ids 必须将下方短语按顺序连续分组，每个短语编号恰好使用一次。
4. key_items 为 1～6 个短词条，每项包含 label 和 trigger_phrase_id。label 是 PPT 上真正显示的文字；trigger_phrase_id 必须属于本页，表示该词条何时出现。
5. page_title_trigger_phrase_id、illustration_trigger_phrase_id、conclusion_trigger_phrase_id 也必须属于本页。只选择短语编号，不输出毫秒或帧号。
6. relationship_type 默认且优先使用 none。只有原文明说“首先→然后→最后”才能用 sequence，明确说“因为→所以/导致”才能用 cause；普通并列清单、训练名称、能力解释一律 none，不得画箭头。
7. role 只能是 overview、detail、transition、summary。layout_type 必须按语义选择：
   - overview：明确列出全文大纲；question：问题、悬念或反问；focus：单一强观点；principle：定义、原则或中心法则；
   - comparison：两方对照；evidence：论点加证据条；layers：多层递进；case：案例或数据；
   - path/flow/timeline：原文明示的步骤、阶段或时间过程；cause：明确因果；cycle：明确循环；summary：编号总结。
   普通 detail 页不能连续三页使用同一 layout_type。
8. series_title 全片一致；同一章节的 chapter_title 连续保持，真正换章节才改变。
9. illustration_elements 只写 1～3 个具体可画主体。插图不负责排版、文字、箭头、连接线或流程关系。

10. 每页遵循“核心观点 → 一句清晰的 page_title → visual_strategy → narrative_link”四步。core_idea 说明本页唯一信息；visual_strategy 说明 PPT 如何呈现；narrative_link 说明它在全文中承上启下的作用。
11. composition 决定页面空间：split-right（文字左、插图右）、split-left（插图左、文字右）、center-stage（中心舞台）、top-bottom（上下结构）、full-width（横向通栏）。相邻页面尽量改变 composition；不得为了变化而违背语义。
12. 同一页不是静态海报：把 key_items 分别绑定到真正说到它们的短语，让标题、插图、关键词、证据和结论逐层累积出现。
{oil_visual_rule}

每页输出：source_phrase_ids、series_title、chapter_title、core_idea、page_title、page_title_trigger_phrase_id、key_text、role、layout_type、composition、relationship_type、key_items、conclusion、conclusion_trigger_phrase_id、visual_strategy、narrative_link、illustration_elements、illustration_trigger_phrase_id。
只返回 JSON 数组，不要解释。

完整短语时间表：
{numbered_phrases}"""
    else:
        prompt = f"""你是中文白板动画分镜导演。下面已经把文案固定拆成 {scene_count} 幕。
总口播时长约 {duration:.1f} 秒。风格：{style}。
严格按幕输出 title、key_text、concept、elements，不要输出或改写原文。
key_text 是给观众看的中文重点短语，必须准确概括本幕原文，只写 4～10 个汉字，不加标点，不得编造原文没有的观点。
elements 必须是恰好 3 个具体可画的中文短语，按叙事顺序排列；每项必须包含主体和动作或物体，禁止使用抽象词。
{character_rule}
{paper_rule}
每幕只讲一个清晰事件，禁止加入原文没有的童年、旅行、花鸟、山水、宠物等内容。
只返回 JSON 数组，不要解释。
固定分幕：
{fixed_segments}"""
    scenes: list[dict[str, Any]] = []
    last_plan_error: Exception | None = None
    for attempt in range(3):
        plan_text = provider_plan_text(
            config, prompt, infographic=infographic,
            item_limit=requested_count if infographic else scene_count, job_id=job_id,
        )
        try:
            candidate = parse_json_block(plan_text)
            if not isinstance(candidate, list) or not candidate:
                raise RuntimeError("分镜模型未返回有效场景")
            if not infographic and len(candidate) != scene_count:
                raise RuntimeError(f"分镜模型返回 {len(candidate)} 幕，预期 {scene_count} 幕")
            if not all(isinstance(scene, dict) for scene in candidate):
                raise RuntimeError("分镜模型返回的数据结构无效")
            if infographic:
                if len(candidate) > requested_count:
                    raise RuntimeError(f"信息图页面超过上限 {requested_count}")
                candidate = normalize_deck_pages(candidate, copy, phrase_timeline or {})
            scenes = candidate
            break
        except (json.JSONDecodeError, RuntimeError, TypeError, ValueError) as exc:
            last_plan_error = exc
            if attempt == 2:
                break
            if job_id and job_id in JOBS:
                update_job(job_id, stage=f"分镜结果异常，正在自动重试 {attempt + 2}/3", model_retry_count=int(JOBS[job_id].get("model_retry_count", 0)) + 1)
            time.sleep(provider_retry_delay(attempt))
    if not scenes:
        raise RuntimeError(f"分镜模型连续 3 次返回无效结果：{last_plan_error}")
    from scripts.add_key_text import clean_key_text
    series_title = ""
    for i, scene in enumerate(scenes):
        if infographic:
            scene["key_text"] = clean_key_text(str(scene.get("key_text") or scene.get("page_title") or "本页重点"), 16)
        else:
            scene["text"] = segments[i]
            key_text = clean_key_text(str(scene.get("key_text") or scene.get("title") or segments[i]), 10)
            scene["key_text"] = key_text or clean_key_text(segments[i], 10) or "本幕重点"
    if not infographic:
        fit_scene_durations(scenes, duration)
    for scene in scenes:
        raw_elements = scene.get("elements") or []
        labels = [str(x.get("label", "")) if isinstance(x, dict) else str(x) for x in raw_elements]
        labels = [x.strip() for x in labels if x.strip()][:4]
        if len(labels) < 2:
            labels = [scene.get("title", "口播主角"), scene.get("concept", "核心事件")]
        scene["elements"] = labels
    return scenes


def build_image_prompt(scene: dict[str, Any], style: str) -> str:
    labels = scene.get("elements") or [scene.get("title", "场景主体")]
    count = len(labels)
    lanes = "；".join(f"第{i + 1}区：{label}" for i, label in enumerate(labels))
    character_instruction = (
        "原文指定的人物或动物身份优先；没有指定身份且确实需要通用讲解角色时，才使用戴细圆框眼镜的圆头极简线人。暖黄边牧仅在语义合适时陪伴，不强制出现。"
        if style == OIL_VISUAL_STYLE else
        "同一主角固定为：中国青年男性，短黑发，朴素深色上衣，普通人形象；不要改变年龄与外貌。"
    )
    return f"""生成一张用于中文口播的 16:9 白板动画分镜原画。
风格名称：{style}。
视觉配方：{style_recipe(style)}
必须严格执行这套视觉配方，不得自动改回其他白板风格；人物、物体和配色都要让所选风格一眼可辨。
本幕标题：{scene.get('title', '')}
本幕叙事：{scene.get('concept', '')}
本幕原文：{scene.get('text', '')}
必须严格表现本幕叙事，不得生成童年成长、旅行、花鸟、山水、宠物等无关意象。
{character_instruction}
构图必须从左到右平均分成 {count} 个互不重叠的独立小场景，每区主体居中，区间有明显留白：{lanes}。
必须把上述每个元素都画出来，顺序不得改变；任何人物或物体不得跨越相邻区域。
主体整体垂直居中并略微靠上，主要人物和物体中心位于画面高度 42%～48%，顶部不得出现大面积无意义空白。
禁止任何文字、字母、数字、Logo、水印、边框、对话框和装饰性填充。画面底部保留约 16% 空白作为字幕安全区。"""


def build_memory_handdraw_prompt(
    scenes: list[dict[str, Any]],
    reference_instruction: str = "",
    use_character_references: bool = False,
) -> str:
    if len(scenes) != 1:
        raise ValueError("岁月回忆手绘风必须一幕一图")
    scene = scenes[0]
    elements = "、".join(scene.get("elements") or [])
    reference_block = f"人物参考规则：{reference_instruction}\n" if reference_instruction else ""
    character_rule = (
        "如画面出现人物，必须保持参考人物的真实年龄、脸型、发型、服装与标志性特征一致，不得年轻化。"
        if use_character_references else
        "人物身份、年龄和关系只能来自原文；不要默认生成年轻讲解者，不得年轻化任何老人。"
    )
    return f"""生成一张 16:9 成人口述史微纪录片的彩色手绘母图。
风格名称：{MEMORY_HANDDRAW_STYLE}。视觉配方：{style_recipe(MEMORY_HANDDRAW_STYLE)}
{reference_block}{character_rule}
本幕标题：{scene.get('title', '')}。本幕事件：{scene.get('concept', '')}。
对应原文：{scene.get('text', '')}。必须出现：{elements}。
只表现这一幕真实记忆；不得擅自补充年代、地点、人物关系、服装、事件结果或怀旧符号。
画面必须有清晰、连续、易提取的深灰黑钢笔轮廓；颜色使用低饱和彩铅轻铺，轮廓与色块边界严格对齐，不用模糊光晕和厚涂。
采用一个主要动作、一个情绪焦点和一至两个环境锚点，镜头克制温暖、复古但不煽情；底部保留约 12% 字幕安全区。
禁止文字、字母、数字、Logo、水印、边框、多格漫画、摄影写实、3D、霓虹、高饱和和儿童卡通。"""


def build_board_prompt(scenes: list[dict[str, Any]], style: str, reference_instruction: str = "", use_character_references: bool = False, infographic: bool = False) -> str:
    if style == MEMORY_HANDDRAW_STYLE:
        if infographic:
            raise ValueError("岁月回忆手绘风仅支持标准制作模式")
        return build_memory_handdraw_prompt(scenes, reference_instruction, use_character_references)
    if infographic:
        scene = scenes[0]
        elements = "、".join(scene.get("illustration_elements") or scene.get("nodes") or [])
        reference_block = f"视觉参考使用规则：{reference_instruction}\n" if reference_instruction else ""
        return f"""生成一张 16:9 中文知识解说视频的独立插画素材。
所选画面风格：{style}。视觉配方：{style_recipe(style)}
{reference_block}必须让画面在 3 秒内认出主体、10 秒内看懂观点证据；不是装饰性配图。
画面只画以下具象内容：{elements}。对应观点：{scene.get('concept', '')}。
PPT 已确定的视觉策略：{scene.get('visual_strategy', '左侧文字，右侧主题插图')}。
插图槽位类型：{scene.get('layout_type', 'focus')} / {scene.get('composition', 'split-right')}。主体比例应适应该槽位；横向通栏可画并列主体，中心舞台突出单一隐喻，分栏槽位保持竖向紧凑。
插画必须是独立、自然融入背景的视觉证据，不画圆角卡片、照片框、界面面板；不要强制添加讲解者或青年男性。
这张图只填入 Remotion PPT 已经确定的插图区域，不负责表达页面结构。禁止箭头、连接线、流程线、项目符号和图表关系。
画面四周保留充足留白，主体不要贴边。禁止任何文字、字母、数字、Logo、水印、边框、字幕和 UI；只有原文确实需要人物时才画人物。"""
    panels: list[str] = []
    for i, scene in enumerate(scenes, 1):
        elements = "、".join(scene.get("elements") or [])
        panels.append(
            f"第{i}区｜标题：{scene.get('title', '')}｜事件：{scene.get('concept', '')}｜"
            f"主结构：{scene.get('visual_structure', '')}｜核心隐喻：{scene.get('metaphor', '')}｜"
            f"必须包含：{elements}｜对应原文：{scene.get('text', '')}"
        )
    panel_text = "\n".join(panels)
    style_instruction = (
        f"视觉配方：{style_recipe(style)}\n{reference_instruction}"
        if style == OIL_VISUAL_STYLE and reference_instruction else
        "严格复现输入风格参考图的配色、线条粗细、材质、造型比例与构图语言；不要复制风格图里原有的人物或事件。"
        if reference_instruction else
        f"视觉配方：{style_recipe(style)}\n必须严格执行这套视觉配方，不得自动改回其他白板风格；人物、物体和配色都要让所选风格一眼可辨。"
    )
    character_instruction = (
        "只使用人物参考组中定义的角色；人物出现时必须保持对应参考图的脸型、发型、年龄、服装和标志性特征一致。"
        if use_character_references else
        "原文指定的人物或动物身份优先；未指定身份且确实需要通用角色时才使用戴细圆框眼镜的圆头极简线人，暖黄边牧仅在语义合适时作为陪伴角色。"
        if style == OIL_VISUAL_STYLE else
        "主角必须严格来自原文；动物、人物身份与年龄不得被替换，同一角色在所有分镜中保持一致。"
        if style == PAPER_METAPHOR_STYLE else
        "同一主角固定为：中国青年男性，短黑发，朴素深色上衣，普通人形象；所有分镜中的年龄与外貌保持一致。"
    )
    reference_block = f"参考图说明：\n{reference_instruction}\n" if reference_instruction else ""
    return f"""{reference_block}生成一张用于中文口播的 16:9 白板动画原画，一张图承载 {len(scenes)} 个连续分镜。
风格名称：{style}。
{style_instruction}
{character_instruction}
画面必须从左到右平均分成 {len(scenes)} 个互不重叠的叙事区域，不画边框；每区内部可以组合人物、动作和关键物体，但不得跨区。
{panel_text}
严格表现上述事件，不得生成原文没有的童年成长、旅行、花鸟、山水、宠物或装饰性意象。
所有区域的主体垂直居中并略微靠上，主要人物和物体中心位于画面高度 42%～48%，顶部不得出现大面积无意义空白。
禁止任何文字、字母、数字、Logo、水印、边框和对话框。画面底部保留约 16% 空白作为字幕安全区。"""


def generate_image(config: dict[str, Any], prompt: str, target: Path, reference_images: list[Path] | None = None, job_id: str | None = None) -> None:
    provider = selected_ai_provider(config)
    if provider == "rightcode":
        def on_rightcode_retry(next_attempt: int) -> None:
            if job_id and job_id in JOBS:
                ensure_job_active(job_id)
                update_job(
                    job_id,
                    stage=f"Right Code 图片服务暂时异常，正在自动重试 {next_attempt}/3",
                    model_retry_count=int(JOBS[job_id].get("model_retry_count", 0)) + 1,
                )

        def on_rightcode_progress(progress: int) -> None:
            if job_id and job_id in JOBS:
                ensure_job_active(job_id)
                update_job(job_id, stage=f"Right Code GPT Image 2 生成中 {progress}%")

        try:
            rightcode_generate_image(
                str(config.get("rightcode_api_key", "")),
                str(config.get("rightcode_image_base_url", "")),
                str(config.get("rightcode_task_base_url", "")),
                str(config.get("rightcode_image_model", "")),
                prompt,
                target,
                list(reference_images or []),
                retry_callback=on_rightcode_retry,
                progress_callback=on_rightcode_progress,
                active_callback=(lambda: ensure_job_active(job_id)) if job_id else None,
            )
        except RightCodeHTTPError as exc:
            raise ProviderHTTPError(exc.status_code, str(exc)) from exc
        return
    if provider == "gemini":
        def on_retry(next_attempt: int) -> None:
            if job_id and job_id in JOBS:
                ensure_job_active(job_id)
                update_job(
                    job_id,
                    stage=f"Gemini 图片服务暂时异常，正在自动重试 {next_attempt}/3",
                    model_retry_count=int(JOBS[job_id].get("model_retry_count", 0)) + 1,
                )

        try:
            gemini_generate_image(
                str(config.get("gemini_api_key", "")),
                str(config.get("gemini_image_model", "")),
                prompt,
                target,
                list(reference_images or []),
                retry_callback=on_retry,
            )
        except GeminiHTTPError as exc:
            raise ProviderHTTPError(exc.status_code, str(exc)) from exc
        return
    # OpenLux documents a 1000-character limit for this GPT Image route.
    compact_prompt = prompt if len(prompt) <= 1000 else f"{prompt[:830]}\n{prompt[-160:]}"
    request_payload = {
        "model": config["image_model"],
        "prompt": compact_prompt,
        "n": 1,
        "size": "1536x1024",
        "quality": "medium",
        "format": "png",
    }
    if reference_images:
        if not config.get("api_key"):
            raise RuntimeError("请先在 API 设置中填写 OpenLux API Key")
        form_data = {key: str(value) for key, value in request_payload.items()}
        raw_files = [(path.name, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "image/png") for path in reference_images]
        response = None
        last_transport_error: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=1800) as client:
                    for field_name in ("image", "image[]"):
                        files = [(field_name, (name, content, mime)) for name, content, mime in raw_files]
                        response = client.post(
                            f"{config['base_url'].rstrip('/')}/images/edits",
                            headers={"Authorization": f"Bearer {config['api_key']}"},
                            data=form_data,
                            files=files,
                        )
                        if not response.is_error or response.status_code not in {400, 422}:
                            break
                if response is not None and not response.is_error:
                    break
                retryable = response is not None and response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
                if not retryable or attempt == 2:
                    break
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_transport_error = exc
                if attempt == 2:
                    raise
            if job_id and job_id in JOBS:
                update_job(job_id, stage=f"参考图模型暂时异常，正在自动重试 {attempt + 2}/3", model_retry_count=int(JOBS[job_id].get("model_retry_count", 0)) + 1)
            time.sleep(provider_retry_delay(attempt))
        if response is None or response.is_error:
            status = response.status_code if response is not None else 500
            detail = response.text[:800] if response is not None else str(last_transport_error or "没有响应")
            raise ProviderHTTPError(status, f"OpenLux 参考图调用失败：{status} {detail}")
        payload = response.json()
    else:
        try:
            payload = provider_post(config, "images/generations", request_payload, timeout=1800, job_id=job_id)
        except ProviderHTTPError as exc:
            if exc.status_code not in {404, 405}:
                raise
            # OpenLux currently also documents GPT Image 2 creation on this route.
            payload = provider_post(config, "images/edits", request_payload, timeout=1800, job_id=job_id)
    candidates = payload.get("data") or payload.get("choices") or []
    if not candidates:
        raise RuntimeError("GPT Image 2 没有返回图像数据")
    item = candidates[0]
    encoded = item.get("b64_json") or item.get("b64")
    url = item.get("url")
    if encoded:
        if isinstance(encoded, str) and encoded.startswith("data:image"):
            encoded = encoded.split(",", 1)[-1]
        target.write_bytes(base64.b64decode(encoded))
    elif url:
        with httpx.Client(timeout=120) as client:
            response = client.get(url)
            response.raise_for_status()
            target.write_bytes(response.content)
    else:
        raise RuntimeError("GPT Image 2 返回格式中没有 b64_json 或 url")


def visual_reference_context(job_id: str) -> tuple[list[Path], str, str]:
    with LOCK:
        job = JOBS.get(job_id, {}).copy()
    reference_mode = str(job.get("reference_mode") or "standard")
    if reference_mode not in {"standard", "custom"}:
        return [], "", ""
    job_dir = JOBS_DIR / job_id
    references = job.get("visual_references") or {}
    paths: list[Path] = []
    lines: list[str] = []
    image_index = 1
    if reference_mode == "custom":
        style_name = str(references.get("style_image") or "")
        style_path = job_dir / style_name
        if not style_name or not valid_image_file(style_path):
            raise RuntimeError("自定义风格参考图缺失或无效")
        paths.append(style_path)
        lines.append("输入图1是唯一的画面风格参考，只学习其视觉风格，不复制图中人物。")
        image_index = 2
    character_descriptions: list[str] = []
    for character in references.get("characters") or []:
        name = str(character.get("name") or "未命名人物")[:20]
        description = str(character.get("description") or "以参考图外观为准")[:80]
        character_paths = [job_dir / str(value) for value in character.get("images") or []]
        character_paths = [path for path in character_paths if valid_image_file(path)]
        if not character_paths:
            continue
        start = image_index
        paths.extend(character_paths)
        image_index += len(character_paths)
        end = image_index - 1
        range_label = f"输入图{start}" if start == end else f"输入图{start}至输入图{end}"
        lines.append(f"{range_label}共同定义人物“{name}”：{description}。同名人物在所有分镜保持一致。")
        character_descriptions.append(f"{name}（{description}）")
    if not character_descriptions:
        if reference_mode == "custom":
            raise RuntimeError("没有可用的人物参考图")
        return [], "", ""
    return paths, "\n".join(lines), "；".join(character_descriptions)


MINIMAX_CLONE_TTL_SECONDS = 7 * 24 * 60 * 60


def reference_audio_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_minimax_voice_cache() -> dict[str, Any]:
    if not VOICE_CACHE_PATH.exists():
        return {"version": 1, "voices": {}}
    try:
        payload = json.loads(VOICE_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "voices": {}}
    voices = payload.get("voices")
    if not isinstance(voices, dict):
        voices = {}
    return {"version": 1, "voices": voices}


def cached_minimax_voice(audio_hash: str, now: float | None = None) -> dict[str, Any] | None:
    current_time = time.time() if now is None else now
    with VOICE_CACHE_LOCK:
        entry = (_load_minimax_voice_cache().get("voices") or {}).get(audio_hash)
        if not isinstance(entry, dict) or not str(entry.get("voice_id") or ""):
            return None
        if bool(entry.get("activated")):
            return entry.copy()
        cloned_at = float(entry.get("cloned_at") or 0)
        if cloned_at and current_time - cloned_at < MINIMAX_CLONE_TTL_SECONDS:
            return entry.copy()
    return None


def remember_minimax_voice(
    audio_hash: str,
    voice_id: str,
    *,
    activated: bool,
    now: float | None = None,
    cloned_at: float | None = None,
) -> None:
    current_time = time.time() if now is None else now
    with VOICE_CACHE_LOCK:
        payload = _load_minimax_voice_cache()
        voices = payload.setdefault("voices", {})
        existing = voices.get(audio_hash) if isinstance(voices.get(audio_hash), dict) else {}
        was_activated = bool(existing.get("activated"))
        entry = {
            "voice_id": voice_id,
            "cloned_at": float(existing.get("cloned_at") or cloned_at or current_time),
            "activated": was_activated or activated,
        }
        if entry["activated"]:
            entry["activated_at"] = float(existing.get("activated_at") or current_time)
        voices[audio_hash] = entry
        STATE_DIR.mkdir(exist_ok=True)
        VOICE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = VOICE_CACHE_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(VOICE_CACHE_PATH)


def historical_minimax_voice(audio_hash: str, current_job_id: str) -> dict[str, Any] | None:
    with LOCK:
        candidates = [
            (candidate_job_id, item.copy())
            for candidate_job_id, item in JOBS.items()
            if candidate_job_id != current_job_id
            and item.get("minimax_voice_cloned")
            and item.get("minimax_voice_id")
        ]
    candidates.sort(key=lambda item: float(item[1].get("created_at", 0)), reverse=True)
    current_time = time.time()
    for candidate_job_id, item in candidates:
        candidate_hash = str(item.get("reference_audio_sha256") or "")
        if not candidate_hash:
            candidate_dir = JOBS_DIR / candidate_job_id
            candidate_reference = next(iter(sorted(candidate_dir.glob("reference.*"))), None)
            if candidate_reference is None:
                continue
            try:
                candidate_hash = reference_audio_sha256(candidate_reference)
            except OSError:
                continue
        if candidate_hash != audio_hash:
            continue
        candidate_dir = JOBS_DIR / candidate_job_id
        activated = any(
            path.exists() and path.stat().st_size > 1024
            for path in (candidate_dir / "voice.wav", candidate_dir / "voice.partial.wav")
        )
        cloned_at = float(item.get("created_at") or current_time)
        if not activated and current_time - cloned_at >= MINIMAX_CLONE_TTL_SECONDS:
            continue
        voice_id = str(item["minimax_voice_id"])
        remember_minimax_voice(
            audio_hash,
            voice_id,
            activated=activated,
            cloned_at=cloned_at,
        )
        return cached_minimax_voice(audio_hash)
    return None


def reusable_minimax_voice(audio_hash: str, current_job_id: str) -> dict[str, Any] | None:
    return cached_minimax_voice(audio_hash) or historical_minimax_voice(audio_hash, current_job_id)


def minimax_voice_id(config: dict[str, Any], job_id: str) -> str:
    prefix = re.sub(r"[^A-Za-z0-9_-]", "", str(config.get("minimax_clone_prefix") or "csboard"))
    prefix = prefix.rstrip("-_") or "csboard"
    if not prefix[0].isalpha():
        prefix = f"v{prefix}"
    return f"{prefix}-{job_id}"[:64].rstrip("-_")


def minimax_retryable_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(token in message for token in (
        "http 429", "http 500", "http 502", "http 503", "http 504",
        "connection", "connecterror", "timed out", "timeout",
    ))


def _upload_minimax_clone_audio(config: dict[str, Any], reference: Path) -> int:
    suffix = reference.suffix.lower()
    if suffix not in {".mp3", ".m4a", ".wav"}:
        raise RuntimeError("MiniMax 克隆音频只支持 MP3、M4A 或 WAV")
    if reference.stat().st_size > 20 * 1024 * 1024:
        raise RuntimeError("MiniMax 克隆音频不能超过 20 MB")
    mime = mimetypes.guess_type(reference.name)[0] or "application/octet-stream"
    base_url = str(config.get("minimax_base_url", "")).strip().rstrip("/")
    with reference.open("rb") as audio, httpx.Client(timeout=180) as client:
        response = client.post(
            f"{base_url}/v1/files/upload",
            headers=minimax_headers(config),
            data={"purpose": "voice_clone"},
            files={"file": (reference.name, audio, mime)},
        )
    payload = minimax_json(response, "clone audio upload")
    file_id = (payload.get("file") or {}).get("file_id")
    if not file_id:
        raise RuntimeError("MiniMax 上传响应中没有 file_id")
    return int(file_id)


def _clone_minimax_voice_once(config: dict[str, Any], reference: Path, voice_id: str) -> None:
    base_url = str(config.get("minimax_base_url", "")).strip().rstrip("/")
    if not base_url:
        raise RuntimeError("MiniMax API 地址不能为空")
    file_id = _upload_minimax_clone_audio(config, reference)
    request_payload = {
        "file_id": file_id,
        "voice_id": voice_id,
        "need_noise_reduction": True,
        "need_volume_normalization": True,
        "aigc_watermark": False,
    }
    with httpx.Client(timeout=180) as client:
        response = client.post(
            f"{base_url}/v1/voice_clone",
            headers=minimax_headers(config),
            json=request_payload,
        )
    minimax_json(response, "voice clone")


def clone_minimax_voice(config: dict[str, Any], reference: Path, voice_id: str) -> None:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            _clone_minimax_voice_once(config, reference, voice_id)
            return
        except Exception as exc:
            last_error = exc
            if not minimax_retryable_error(exc) or attempt == 2:
                break
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"MiniMax 音色克隆失败：{last_error or '未知错误'}") from last_error


def _synthesize_minimax_voice_once(config: dict[str, Any], copy: str, target: Path, voice_id: str) -> None:
    if str(config.get("minimax_api_type")) != "t2a_v2":
        raise RuntimeError("目前只支持 MiniMax T2A v2")
    base_url = str(config.get("minimax_base_url", "")).strip().rstrip("/")
    if not base_url:
        raise RuntimeError("MiniMax API 地址不能为空")
    if not copy.strip() or len(copy) >= 10000:
        raise RuntimeError("MiniMax 同步语音合成文本必须少于 10000 字符")
    request_payload = {
        "model": str(config.get("minimax_speech_model") or "speech-2.8-hd"),
        "text": copy,
        "stream": False,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": 1,
            "vol": 1,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "wav",
            "channel": 1,
        },
        "language_boost": "Chinese",
        "subtitle_enable": False,
        "output_format": "hex",
        "aigc_watermark": False,
    }
    with httpx.Client(timeout=300) as client:
        response = client.post(
            f"{base_url}/v1/t2a_v2",
            headers=minimax_headers(config),
            json=request_payload,
        )
    payload = minimax_json(response, "speech synthesis")
    audio_hex = str((payload.get("data") or {}).get("audio") or "")
    if not audio_hex:
        raise RuntimeError("MiniMax 合成响应中没有音频数据")
    try:
        target.write_bytes(bytes.fromhex(audio_hex))
    except ValueError as exc:
        raise RuntimeError("MiniMax 返回了无效的十六进制音频数据") from exc


def synthesize_voice(config: dict[str, Any], copy: str, target: Path, voice_id: str) -> None:
    """Use a cloned MiniMax voice and preserve the pipeline's WAV contract."""
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            _synthesize_minimax_voice_once(config, copy, target, voice_id)
            return
        except Exception as exc:
            last_error = exc
            if not minimax_retryable_error(exc) or attempt == 3:
                break
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"MiniMax 语音合成失败：{last_error or '未知错误'}") from last_error

def write_annotation(scene: dict[str, Any], image: Path, target: Path, index: int) -> None:
    from PIL import Image

    with Image.open(image) as im:
        width, height = im.size
    labels = scene.get("elements") or [scene.get("title", "场景主体")]
    count = max(1, len(labels))
    duration = int(scene["duration_ms"])
    gap = 120
    usable = duration - 500 - gap * (count - 1)
    each = max(500, usable // count)
    margin_x = max(10, width // 80)
    band = (width - margin_x * 2) / count
    elements = []
    for i, label in enumerate(labels):
        x = round(margin_x + i * band)
        x2 = round(margin_x + (i + 1) * band)
        start = 200 + i * (each + gap)
        elements.append({
            "id": f"part-{i+1}", "label": str(label), "sequence": i + 1,
            "narrativeRole": "按文案叙事顺序出现", "subtitle": scene.get("text", ""), "type": "concept",
            "region": {"x": x, "y": round(height * 0.02), "width": x2 - x, "height": round(height * 0.80)},
            "reveal": {"direction": "left_to_right", "startMs": start, "durationMs": each, "maskPaddingPx": 16, "protectedRegions": []},
            "handPath": {"start": [x + 5, height // 2], "end": [x2 - 5, height // 2], "easing": "easeInOut"},
        })
    data = {
        "sceneId": f"scene-{index:02d}", "canvas": {"width": width, "height": height},
        "storyBasis": scene.get("concept", scene.get("title", "")), "sceneDurationMs": duration,
        "elements": elements,
    }
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_board_annotation(scenes: list[dict[str, Any]], image: Path, target: Path, index: int) -> None:
    from PIL import Image

    with Image.open(image) as im:
        width, height = im.size
    count = len(scenes)
    margin_x = max(10, width // 100)
    band = (width - margin_x * 2) / count
    offset = 0
    elements = []
    for i, scene in enumerate(scenes):
        x = round(margin_x + i * band)
        x2 = round(margin_x + (i + 1) * band)
        duration = int(scene["duration_ms"])
        elements.append({
            "id": f"panel-{i + 1}", "label": scene.get("title", f"分镜{i + 1}"),
            "sequence": i + 1, "narrativeRole": scene.get("concept", "按原文叙事"),
            "subtitle": scene.get("text", ""), "type": "scene",
            "region": {"x": x, "y": round(height * 0.02), "width": x2 - x, "height": round(height * 0.80)},
            "reveal": {"direction": "left_to_right", "startMs": offset, "durationMs": duration, "maskPaddingPx": 14, "protectedRegions": []},
            "handPath": {"start": [x + 5, height // 2], "end": [x2 - 5, height // 2], "easing": "easeInOut"},
        })
        offset += duration
    data = {
        "sceneId": f"board-{index:02d}", "canvas": {"width": width, "height": height},
        "storyBasis": " / ".join(str(s.get("title", "")) for s in scenes),
        "sceneDurationMs": offset, "elements": elements,
    }
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def make_branded_hand(text: str, target: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    if not text.strip():
        return HAND
    hand = Image.open(HAND).convert("RGBA")
    label = text.strip()[:12]
    font_paths = [Path("C:/Windows/Fonts/msyhbd.ttc"), Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/simhei.ttf")]
    font_path = next((p for p in font_paths if p.exists()), None)
    font = ImageFont.truetype(str(font_path), 58) if font_path else ImageFont.load_default()
    strip = Image.new("RGBA", (430, 104), (0, 0, 0, 0))
    draw = ImageDraw.Draw(strip)
    box = draw.textbbox((0, 0), label, font=font)
    text_width = box[2] - box[0]
    if text_width > 380 and font_path:
        font = ImageFont.truetype(str(font_path), max(24, round(58 * 380 / text_width)))
        box = draw.textbbox((0, 0), label, font=font)
        text_width = box[2] - box[0]
    draw.text(((430 - text_width) / 2, 20), label, font=font, fill=(105, 48, 30, 240), stroke_width=1, stroke_fill=(255, 255, 255, 200))
    rotated = strip.rotate(-40, resample=Image.Resampling.BICUBIC, expand=True)
    hand.alpha_composite(rotated, (430, 300))
    hand.save(target)
    return target


def _subtitle_chunks(text: str, max_chars: int = 22) -> list[str]:
    sentences = [x.strip() for x in re.findall(r"[^。！？!?；;，,]+[。！？!?；;，,]?", text) if x.strip()]
    chunks: list[str] = []
    for sentence in sentences:
        while len(sentence) > max_chars:
            chunks.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        if sentence:
            chunks.append(sentence)
    return chunks or [text]


def _srt_time(ms: int) -> str:
    hours, rem = divmod(max(0, ms), 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def write_subtitles(scenes: list[dict[str, Any]], target: Path) -> None:
    aligned_cues = [
        cue
        for scene in scenes
        for cue in (scene.get("subtitle_cues") or [])
        if isinstance(cue, dict)
    ]
    if aligned_cues and all(scene.get("subtitle_cues") for scene in scenes):
        lines: list[str] = []
        for index, cue in enumerate(aligned_cues, 1):
            lines.extend([
                str(index),
                f"{_srt_time(int(cue['start_ms']))} --> {_srt_time(int(cue['end_ms']))}",
                str(cue["text"]),
                "",
            ])
        target.write_text("\n".join(lines), encoding="utf-8")
        return
    cues: list[tuple[int, int, str]] = []
    offset = 0
    for scene in scenes:
        chunks = _subtitle_chunks(str(scene.get("text", "")))
        weights = [max(1, len(re.sub(r"\s+", "", x))) for x in chunks]
        duration = int(scene["duration_ms"])
        used = 0
        for i, (chunk, weight) in enumerate(zip(chunks, weights)):
            cue_ms = duration - used if i == len(chunks) - 1 else round(duration * weight / sum(weights))
            cues.append((offset + used, offset + used + cue_ms, chunk))
            used += cue_ms
        offset += duration
    lines: list[str] = []
    for i, (start, end, text) in enumerate(cues, 1):
        lines.extend([str(i), f"{_srt_time(start)} --> {_srt_time(end)}", text, ""])
    target.write_text("\n".join(lines), encoding="utf-8")


def remotion_infographic_props(scenes: list[dict[str, Any]], style: str, duration_ms: int, subtitles_enabled: bool = False) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes, 1):
        timed_cues = scene.get("timed_cues")
        if not isinstance(timed_cues, list) or not timed_cues:
            raise RuntimeError(f"第 {index} 页没有通过真实语音对齐，禁止进入 Remotion 渲染")
        layout_type = str(scene.get("layout_type") or "focus")
        pages.append({
            "id": f"page-{index}",
            "image": f"board-{index:02d}.png",
            "startFrame": int(scene["start_frame"]),
            "endFrame": int(scene["end_frame"]),
            "seriesTitle": str(scene.get("series_title") or "动态知识解说"),
            "chapterTitle": str(scene.get("chapter_title") or "本章要点"),
            "pageTitle": str(scene.get("page_title") or scene.get("key_text") or "核心观点"),
            "layoutType": layout_type,
            "composition": str(scene.get("composition") or _default_composition(layout_type, index)),
            "slideRole": str(scene.get("role") or "detail"),
            "relationshipType": str(scene.get("relationship_type") or "none"),
            "coreIdea": str(scene.get("core_idea") or scene.get("concept") or ""),
            "visualStrategy": str(scene.get("visual_strategy") or ""),
            "narrativeLink": str(scene.get("narrative_link") or ""),
            "nodes": [str(value) for value in scene.get("nodes") or []],
            "conclusion": str(scene.get("conclusion") or ""),
            "seriesPersistent": bool(scene.get("series_persistent")),
            "chapterPersistent": bool(scene.get("chapter_persistent")),
            "cues": [{
                "id": str(cue["id"]),
                "anchorText": str(cue["anchor_text"]),
                "startFrame": int(cue["start_frame"]),
                "endFrame": int(cue["end_frame"]),
                "spokenStartMs": int(cue["spoken_start_ms"]),
                "spokenEndMs": int(cue["spoken_end_ms"]),
                "enterIds": [str(value) for value in cue["enter_ids"]],
                "focusId": str(cue["focus_id"]),
                "alignmentCoverage": float(cue["alignment_coverage"]),
                "alignmentConfidence": float(cue["alignment_confidence"]),
            } for cue in timed_cues],
        })
    return {
        "fps": 30,
        "width": 1920,
        "height": 1080,
        "totalDurationMs": duration_ms,
        "totalDurationFrames": max(1, math.ceil(duration_ms * 30 / 1000)),
        "style": style,
        "subtitlesEnabled": subtitles_enabled,
        "pages": pages,
    }


def remotion_memory_props(boards: list[list[dict[str, Any]]], duration_ms: int) -> dict[str, Any]:
    fps = 30
    total_frames = max(1, round(duration_ms * fps / 1000))
    elapsed_ms = 0
    memory_scenes: list[dict[str, Any]] = []
    for index, board in enumerate(boards, 1):
        scene_duration_ms = sum(int(scene.get("duration_ms") or 0) for scene in board)
        if scene_duration_ms <= 0:
            raise RuntimeError(f"第 {index} 个回忆场景缺少有效时长")
        start_frame = round(elapsed_ms * fps / 1000)
        elapsed_ms += scene_duration_ms
        end_frame = total_frames if index == len(boards) else round(elapsed_ms * fps / 1000)
        memory_scenes.append({
            "id": f"memory-{index}",
            "lineImage": f"board-{index:02d}.line.png",
            "colorImage": f"board-{index:02d}.source.png",
            "startFrame": start_frame,
            "endFrame": max(start_frame + 1, end_frame),
        })
    return {
        "compositionId": "MemoryHanddraw",
        "fps": fps,
        "width": 1920,
        "height": 1080,
        "totalDurationMs": duration_ms,
        "totalDurationFrames": total_frames,
        "paperColor": "#f7f2e8",
        "scenes": memory_scenes,
    }


def fail_job(job_id: str, stage: str, exc: Exception) -> None:
    if isinstance(exc, JobCancelled) or is_job_cancelled(job_id):
        return
    finish_timing(job_id)
    update_job(job_id, status="error", stage=stage, error=str(exc))


def voice_stage(job_id: str, copy: str, style: str, reference: Path, scenes_per_image: int, pen_text: str, include_key_text: bool, include_subtitles: bool, stroke_detail: str, service_url: str, node_index: int) -> None:
    job_dir = JOBS_DIR / job_id
    try:
        config = load_config()
        config["minimax_base_url"] = service_url
        with LOCK:
            job = JOBS.get(job_id, {}).copy()
        audio_hash = reference_audio_sha256(reference)
        own_clone_ready = bool(job.get("minimax_voice_cloned") and job.get("minimax_voice_id"))
        cached_voice = None if own_clone_ready else reusable_minimax_voice(audio_hash, job_id)
        if own_clone_ready:
            voice_id = str(job["minimax_voice_id"])
            clone_ready = True
            reused_voice = bool(job.get("minimax_voice_reused"))
        elif cached_voice:
            voice_id = str(cached_voice["voice_id"])
            clone_ready = True
            reused_voice = True
        else:
            voice_id = str(job.get("minimax_voice_id") or minimax_voice_id(config, job_id))
            clone_ready = False
            reused_voice = False
        update_job(
            job_id,
            voice_provider="MiniMax",
            voice_service=service_url,
            minimax_voice_id=voice_id,
            minimax_voice_cloned=clone_ready,
            minimax_voice_reused=reused_voice,
            reference_audio_sha256=audio_hash,
        )
        voice = job_dir / "voice.wav"
        if not valid_media_file(voice):
            if not clone_ready:
                begin_phase(job_id, "voice_clone", "MiniMax 音色克隆", "正在上传首次出现的参考音频并创建克隆音色", 6)
                clone_minimax_voice(config, reference, voice_id)
                ensure_job_active(job_id)
                remember_minimax_voice(audio_hash, voice_id, activated=False)
                update_job(job_id, minimax_voice_cloned=True)
            voice_message = "已复用相同参考音频的克隆音色，正在生成完整旁白" if reused_voice else "正在用克隆音色生成完整旁白"
            begin_phase(job_id, "voice", "MiniMax 语音合成", voice_message, 9)
            partial_voice = job_dir / "voice.partial.wav"
            partial_voice.unlink(missing_ok=True)
            synthesize_voice(config, copy, partial_voice, voice_id)
            remember_minimax_voice(audio_hash, voice_id, activated=True)
            ensure_job_active(job_id)
            if not valid_media_file(partial_voice):
                raise RuntimeError("MiniMax 返回的音频文件无效")
            partial_voice.replace(voice)
        else:
            remember_minimax_voice(audio_hash, voice_id, activated=True)
        duration = probe_duration(voice)
        update_job(job_id, duration=duration, checkpoint="voice_done")
        queue_for_stage(job_id, "model", "等待调用模型", 14)
        MODEL_QUEUE.put((job_id, copy, style, reference, scenes_per_image, pen_text, include_key_text, include_subtitles, stroke_detail))
        ensure_pipeline_workers()
    except Exception as exc:
        fail_job(job_id, "MiniMax 配音失败", exc)
def model_stage(job_id: str, copy: str, style: str, reference: Path, scenes_per_image: int, pen_text: str, include_key_text: bool, include_subtitles: bool, stroke_detail: str) -> None:
    job_dir = JOBS_DIR / job_id
    try:
        config = model_config_for_job(job_id)
        voice = job_dir / "voice.wav"
        duration = probe_duration(voice)
        reference_images, reference_instruction, character_context = visual_reference_context(job_id)
        infographic = is_infographic_job(job_id)

        phrase_timeline: dict[str, Any] | None = None
        if infographic:
            begin_phase(job_id, "alignment", "短语时间表", "正在制作完整的短语—真实旁白时间 JSON", 16)
            alignment_path = job_dir / "alignment.tokens.json"
            desired_alignment_model = os.environ.get("INFOGRAPHIC_WHISPER_MODEL", "medium")
            alignment_current = False
            if alignment_path.exists():
                try:
                    saved_alignment = json.loads(alignment_path.read_text(encoding="utf-8"))
                    alignment_current = (
                        str(saved_alignment.get("model") or "") == desired_alignment_model
                        and saved_alignment.get("segmentation") == ALIGNMENT_SEGMENTATION
                        and bool(saved_alignment.get("speechSegments"))
                    )
                except (OSError, json.JSONDecodeError):
                    alignment_current = False
            if not alignment_current:
                run([
                    str(NODE),
                    str(REMOTION_RENDERER / "align.mjs"),
                    str(voice),
                    str(alignment_path),
                ], cwd=REMOTION_RENDERER, job_id=job_id)
            try:
                alignment_payload = json.loads(alignment_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError("真实旁白 token 时间戳文件损坏，请删除后重试") from exc
            from scripts.semantic_timeline import build_phrase_timeline
            phrase_timeline = build_phrase_timeline(copy, alignment_payload, round(duration * 1000), fps=30)
            atomic_write_json(job_dir / "phrase-timeline.json", phrase_timeline)
            update_job(job_id, checkpoint="phrase_timeline_done")

        plan_path = job_dir / "plan.json"
        scenes: list[dict[str, Any]] = []
        if plan_path.exists():
            try:
                saved_plan = json.loads(plan_path.read_text(encoding="utf-8"))
                valid_modes = {"narrated_deck_v4", "narrated_deck_v4_timed"}
                valid_mode = not infographic or all(scene.get("_plan_mode") in valid_modes for scene in saved_plan if isinstance(scene, dict))
                if isinstance(saved_plan, list) and saved_plan and all(isinstance(scene, dict) for scene in saved_plan) and valid_mode:
                    scenes = saved_plan
            except (OSError, json.JSONDecodeError):
                scenes = []
        if not scenes:
            begin_phase(job_id, "planning", "内容结构", "正在浓缩中心句、列出关键词并规划 PPT 页面", 25)
            scenes = make_plan(config, copy, duration, style, character_context, job_id, infographic, phrase_timeline)
            ensure_job_active(job_id)
            atomic_write_json(plan_path, scenes)
        else:
            begin_phase(job_id, "planning", "内容结构", "已恢复 PPT 内容结构", 27)
        if infographic:
            begin_phase(job_id, "deck", "Remotion PPT", "正在把页面结构和关键词绑定到短语时间", 31)
            from scripts.semantic_timeline import build_deck_timeline
            scenes, alignment_report = build_deck_timeline(
                copy,
                scenes,
                phrase_timeline or {},
                round(duration * 1000),
                fps=30,
            )
            atomic_write_json(plan_path, scenes)
            atomic_write_json(job_dir / "alignment-report.json", alignment_report)
            deck_spec = remotion_infographic_props(scenes, style, round(duration * 1000), include_subtitles)
            atomic_write_json(job_dir / "deck-spec.json", deck_spec)
            atomic_write_json(job_dir / "content-timeline.json", {
                "schema_version": 1,
                "slides": [{
                    "page": index,
                    "source_phrase_ids": scene.get("source_phrase_ids"),
                    "core_idea": scene.get("core_idea"),
                    "page_title": scene.get("page_title"),
                    "key_items": scene.get("key_items"),
                    "layout_type": scene.get("layout_type"),
                    "composition": scene.get("composition"),
                    "visual_strategy": scene.get("visual_strategy"),
                    "narrative_link": scene.get("narrative_link"),
                    "relationship_type": scene.get("relationship_type"),
                    "timed_cues": scene.get("timed_cues"),
                } for index, scene in enumerate(scenes, 1)],
            })
            scenes_per_image = 1
        elif fit_scene_durations(scenes, duration):
            atomic_write_json(plan_path, scenes)
        boards = [scenes[i:i + scenes_per_image] for i in range(0, len(scenes), scenes_per_image)]
        board_specs: list[tuple[list[Path], str, str]] = []
        for board in boards:
            board_images = reference_images
            board_instruction = reference_instruction
            use_character_references = bool(character_context)
            if style == PAPER_METAPHOR_STYLE and not board_images:
                board_images, board_instruction = paper_metaphor_reference_context(board)
                use_character_references = False
            elif style == OIL_VISUAL_STYLE and not board_images:
                board_images, board_instruction = oil_visual_reference_context(board, infographic)
                use_character_references = False
            board_prompt = build_board_prompt(board, style, board_instruction, use_character_references, infographic)
            board_specs.append((board_images, board_instruction, board_prompt))
        update_job(job_id, duration=duration, scenes=len(scenes), boards=len(boards), checkpoint="plan_done")
        atomic_write_json(job_dir / "boards.json", [
            {
                "scene_numbers": list(range(i * scenes_per_image + 1, i * scenes_per_image + len(board) + 1)),
                "image_prompt": board_specs[i][2],
                **({
                    "color_image": f"board-{i + 1:02d}.source.png",
                    "line_image": f"board-{i + 1:02d}.line.png",
                } if style == MEMORY_HANDDRAW_STYLE else {}),
            }
            for i, board in enumerate(boards)
        ])
        from scripts.add_key_text import add_key_text
        for i, board in enumerate(boards, 1):
            board_images, _board_instruction, board_prompt = board_specs[i - 1]
            base_progress = 36 + int((i - 1) / len(boards) * 40)
            begin_phase(job_id, "images", "PPT 插图", f"正在按第 {i}/{len(boards)} 页已确定的插图槽位生成插画", base_progress)
            stem = f"board-{i:02d}"
            image = job_dir / f"{stem}.png"
            source_image = job_dir / f"{stem}.source.png"
            line_image = job_dir / f"{stem}.line.png"
            if not valid_image_file(source_image):
                partial_image = job_dir / f"{stem}.source.partial.png"
                last_image_error: Exception | None = None
                for attempt in range(3):
                    partial_image.unlink(missing_ok=True)
                    try:
                        generate_image(config, board_prompt, partial_image, board_images, job_id)
                        ensure_job_active(job_id)
                        if valid_image_file(partial_image):
                            break
                        raise RuntimeError("模型返回的图片文件无效")
                    except JobCancelled:
                        raise
                    except ProviderHTTPError:
                        raise
                    except (RuntimeError, ValueError, OSError) as exc:
                        last_image_error = exc
                        if attempt == 2:
                            break
                        update_job(job_id, stage=f"第 {i} 张图片结果异常，正在自动重试 {attempt + 2}/3", model_retry_count=int(JOBS[job_id].get("model_retry_count", 0)) + 1)
                        time.sleep(provider_retry_delay(attempt))
                if not valid_image_file(partial_image):
                    raise RuntimeError(f"第 {i} 张分镜图连续 3 次生成无效：{last_image_error}")
                if style == MEMORY_HANDDRAW_STYLE:
                    partial_line = job_dir / f"{stem}.line.partial.png"
                    partial_line.unlink(missing_ok=True)
                    ensure_memory_lineart_asset(style, partial_image, partial_line)
                partial_image.replace(source_image)
                if style == MEMORY_HANDDRAW_STYLE:
                    partial_line.replace(line_image)
            if style == MEMORY_HANDDRAW_STYLE and not aligned_image_pair(source_image, line_image):
                ensure_memory_lineart_asset(style, source_image, line_image)
            if style == MEMORY_HANDDRAW_STYLE:
                shutil.copy2(source_image, image)
            elif include_key_text and not infographic:
                add_key_text(source_image, [str(scene.get("key_text", "")) for scene in board], image)
            else:
                shutil.copy2(source_image, image)
            update_job(job_id, checkpoint="images", completed_boards=i)
        queue_for_stage(job_id, "render", "准备本地渲染", 78)
        start_render_task(render_generated_job, job_id, scenes, boards, pen_text, include_subtitles, stroke_detail, duration)
    except Exception as exc:
        current_phase = str(JOBS.get(job_id, {}).get("current_phase") or "")
        stage = {
            "alignment": "短语时间表失败",
            "planning": "内容结构失败",
            "deck": "Remotion PPT 结构失败",
            "images": "PPT 插图生成失败",
        }.get(current_phase, "模型调用失败")
        fail_job(job_id, stage, exc)


def render_generated_job(job_id: str, scenes: list[dict[str, Any]], boards: list[list[dict[str, Any]]], pen_text: str, include_subtitles: bool, stroke_detail: str, duration: float) -> None:
    job_dir = JOBS_DIR / job_id
    try:
        infographic = is_infographic_job(job_id)
        style = str(JOBS.get(job_id, {}).get("style") or DEFAULT_STYLE)
        memory_handdraw = style == MEMORY_HANDDRAW_STYLE
        duration_ms = round(duration * 1000)
        if infographic:
            begin_phase(job_id, "drawing", "Remotion 渲染", "正在按真实旁白时间编排动态信息图", 80)
            silent = job_dir / "silent-remotion-v1.mp4"
            final = job_dir / "final-remotion-v1.mp4"
            if not valid_timed_video(silent, duration_ms):
                partial_silent = job_dir / "silent-remotion-v1.partial.mp4"
                partial_silent.unlink(missing_ok=True)
                props_path = job_dir / "remotion-props.json"
                atomic_write_json(
                    props_path,
                    remotion_infographic_props(
                        scenes,
                        str(JOBS.get(job_id, {}).get("style") or DEFAULT_STYLE),
                        duration_ms,
                        include_subtitles,
                    ),
                )
                run([
                    str(NODE),
                    str(REMOTION_RENDERER / "render.mjs"),
                    str(props_path),
                    str(partial_silent),
                    str(job_dir),
                ], cwd=REMOTION_RENDERER, job_id=job_id)
                if not valid_timed_video(partial_silent, duration_ms):
                    raise RuntimeError("Remotion 信息图视频时长与真实旁白不一致")
                partial_silent.replace(silent)
            update_job(job_id, checkpoint="render", completed_videos=len(scenes), render_engine="remotion-semantic-v1")
        elif memory_handdraw:
            begin_phase(job_id, "drawing", "Remotion 回忆手绘", "正在按真实旁白时间推进线稿与上色", 80)
            for index in range(1, len(boards) + 1):
                color_image = job_dir / f"board-{index:02d}.source.png"
                line_image = job_dir / f"board-{index:02d}.line.png"
                if not aligned_image_pair(color_image, line_image):
                    raise RuntimeError(f"第 {index} 组线稿和彩色图缺失或尺寸不一致")
            silent = job_dir / "silent-memory-remotion-v1.mp4"
            final = job_dir / "final-memory-remotion-v1.mp4"
            if not valid_timed_video(silent, duration_ms):
                partial_silent = job_dir / "silent-memory-remotion-v1.partial.mp4"
                partial_silent.unlink(missing_ok=True)
                props_path = job_dir / "memory-remotion-props.json"
                atomic_write_json(props_path, remotion_memory_props(boards, duration_ms))
                run([
                    str(NODE),
                    str(REMOTION_RENDERER / "render.mjs"),
                    str(props_path),
                    str(partial_silent),
                    str(job_dir),
                ], cwd=REMOTION_RENDERER, job_id=job_id)
                if not valid_timed_video(partial_silent, duration_ms):
                    raise RuntimeError("Remotion 回忆手绘视频时长与真实旁白不一致")
                partial_silent.replace(silent)
            update_job(job_id, checkpoint="render", completed_videos=len(boards), render_engine="remotion-memory-wipe-v1")
        else:
            hand_asset = make_branded_hand(pen_text, job_dir / "hand-branded.png")
            videos: list[Path] = []
            for i, board in enumerate(boards, 1):
                progress = 78 + int((i - 1) / len(boards) * 12)
                begin_phase(job_id, "drawing", "手绘渲染", f"正在绘制第 {i}/{len(boards)} 张分镜图", progress)
                stem = f"board-{i:02d}"
                image = job_dir / f"{stem}.png"
                annotation = job_dir / f"{stem}.annotation.json"
                video = job_dir / f"{stem}.mp4"
                expected_ms = sum(int(scene["duration_ms"]) for scene in board)
                if not valid_timed_video(video, expected_ms):
                    video.unlink(missing_ok=True)
                    partial_video = job_dir / f"{stem}.partial.mp4"
                    partial_video.unlink(missing_ok=True)
                    write_board_annotation(board, image, annotation, i)
                    run([str(PYTHON), str(ROOT / "scripts" / "render_stream_whiteboard.py"), str(image), str(annotation), str(partial_video), str(hand_asset), "--ink-path", "skeleton", "--stroke-detail", stroke_detail, "--color-fill", "contour-wipe"], job_id=job_id)
                    if not valid_media_file(partial_video):
                        raise RuntimeError(f"第 {i} 段手绘视频无效")
                    partial_video.replace(video)
                videos.append(video)
                update_job(job_id, checkpoint="render", completed_videos=i)

            silent = job_dir / "silent.mp4"
            final = job_dir / "final.mp4"
            if not valid_media_file(silent):
                partial_silent = job_dir / "silent.partial.mp4"
                partial_silent.unlink(missing_ok=True)
                run([str(PYTHON), str(ROOT / "scripts" / "merge_scenes.py"), "--inputs", *map(str, videos), "--output", str(partial_silent)], job_id=job_id)
                if not valid_media_file(partial_silent):
                    raise RuntimeError("合并后的无声视频无效")
                partial_silent.replace(silent)

        begin_phase(job_id, "compositing", "音画合成", "正在合成声音和画面", 92)
        if not valid_media_file(final):
            partial_final = job_dir / f"{final.stem}.partial.mp4"
            partial_final.unlink(missing_ok=True)
            ffmpeg_command = [str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error", "-i", silent.name, "-i", "voice.wav", "-map", "0:v:0", "-map", "1:a:0"]
            if include_subtitles:
                subtitles = job_dir / "subtitles.srt"
                write_subtitles(scenes, subtitles)
                subtitle_filter = "subtitles=subtitles.srt:force_style='FontName=Microsoft YaHei,FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00202020,BorderStyle=1,Outline=2,Shadow=0,MarginV=28,Alignment=2'"
                ffmpeg_command.extend(["-vf", subtitle_filter])
            ffmpeg_command.extend(["-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest", partial_final.name])
            run(ffmpeg_command, cwd=job_dir, job_id=job_id)
            if not valid_media_file(partial_final):
                raise RuntimeError("最终音画文件无效")
            partial_final.replace(final)
        finish_timing(job_id)
        update_job(job_id, status="done", stage="制作完成", progress=100, result_url=f"/api/jobs/{job_id}/download", result_file=final.name, duration=duration, scenes=len(scenes), boards=len(boards), can_rerender=True)
    except Exception as exc:
        fail_job(job_id, "本地渲染失败", exc)


def rerender_job(job_id: str, scenes_per_image: int, pen_text: str, include_key_text: bool, include_subtitles: bool, stroke_detail: str) -> None:
    job_dir = JOBS_DIR / job_id
    try:
        scenes = json.loads((job_dir / "plan.json").read_text(encoding="utf-8"))
        voice = job_dir / "voice.wav"
        duration = probe_duration(voice)
        if fit_scene_durations(scenes, duration):
            atomic_write_json(job_dir / "plan.json", scenes)
        boards = [scenes[i:i + scenes_per_image] for i in range(0, len(scenes), scenes_per_image)]
        style = str(JOBS.get(job_id, {}).get("style") or DEFAULT_STYLE)
        if style == MEMORY_HANDDRAW_STYLE:
            (job_dir / "silent-memory-remotion-v1.mp4").unlink(missing_ok=True)
            (job_dir / "final-memory-remotion-v1.mp4").unlink(missing_ok=True)
            render_generated_job(job_id, scenes, boards, pen_text, include_subtitles, stroke_detail, duration)
            return
        hand_asset = make_branded_hand(pen_text, job_dir / "hand-branded.png")
        from scripts.add_key_text import add_key_text
        videos: list[Path] = []
        for i, board in enumerate(boards, 1):
            progress = 15 + int(i / len(boards) * 68)
            begin_phase(job_id, "drawing", "重新手绘", f"正在重新绘制第 {i}/{len(boards)} 张分镜图", progress)
            stem = f"board-{i:02d}"
            image = job_dir / f"{stem}.png"
            source_image = job_dir / f"{stem}.source.png"
            annotation = job_dir / f"{stem}.annotation.json"
            video = job_dir / f"{stem}.mp4"
            if source_image.exists():
                if include_key_text:
                    add_key_text(source_image, [str(scene.get("key_text", "")) for scene in board], image)
                else:
                    shutil.copy2(source_image, image)
            if not image.exists():
                raise RuntimeError(f"缺少可复用的分镜图：{image.name}")
            write_board_annotation(board, image, annotation, i)
            expected_ms = sum(int(scene["duration_ms"]) for scene in board)
            if not valid_timed_video(video, expected_ms):
                video.unlink(missing_ok=True)
                partial_video = job_dir / f"{stem}.partial.mp4"
                partial_video.unlink(missing_ok=True)
                run([str(PYTHON), str(ROOT / "scripts" / "render_stream_whiteboard.py"), str(image), str(annotation), str(partial_video), str(hand_asset), "--ink-path", "skeleton", "--stroke-detail", stroke_detail, "--color-fill", "contour-wipe"], job_id=job_id)
                if not valid_media_file(partial_video):
                    raise RuntimeError(f"第 {i} 段重新渲染视频无效")
                partial_video.replace(video)
            videos.append(video)
            update_job(job_id, checkpoint="rerender", completed_videos=i)

        begin_phase(job_id, "compositing", "音画合成", "正在重新合成声音和画面", 90)
        silent = job_dir / "silent.mp4"
        if not valid_media_file(silent):
            partial_silent = job_dir / "silent.partial.mp4"
            partial_silent.unlink(missing_ok=True)
            run([str(PYTHON), str(ROOT / "scripts" / "merge_scenes.py"), "--inputs", *map(str, videos), "--output", str(partial_silent)], job_id=job_id)
            if not valid_media_file(partial_silent):
                raise RuntimeError("重新合并后的无声视频无效")
            partial_silent.replace(silent)
        final = job_dir / "final.mp4"
        if not valid_media_file(final):
            partial_final = job_dir / "final.partial.mp4"
            partial_final.unlink(missing_ok=True)
            ffmpeg_command = [str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error", "-i", "silent.mp4", "-i", "voice.wav", "-map", "0:v:0", "-map", "1:a:0"]
            if include_subtitles:
                subtitles = job_dir / "subtitles.srt"
                write_subtitles(scenes, subtitles)
                subtitle_filter = "subtitles=subtitles.srt:force_style='FontName=Microsoft YaHei,FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00202020,BorderStyle=1,Outline=2,Shadow=0,MarginV=28,Alignment=2'"
                ffmpeg_command.extend(["-vf", subtitle_filter])
            ffmpeg_command.extend(["-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest", partial_final.name])
            run(ffmpeg_command, cwd=job_dir, job_id=job_id)
            if not valid_media_file(partial_final):
                raise RuntimeError("重新渲染的最终音画文件无效")
            partial_final.replace(final)
        finish_timing(job_id)
        update_job(job_id, status="done", stage="重新渲染完成", progress=100, result_url=f"/api/jobs/{job_id}/download", duration=duration, scenes=len(scenes), boards=len(boards), can_rerender=True)
    except Exception as exc:
        fail_job(job_id, "重新渲染失败", exc)


def voice_queue_worker(node_index: int) -> None:
    while True:
        nodes = configured_voice_services()
        if node_index >= len(nodes):
            time.sleep(1)
            continue
        task = VOICE_QUEUE.get()
        try:
            job_id = str(task[0])
            with LOCK:
                should_run = JOBS.get(job_id, {}).get("status") in {"queued", "running"}
            if not should_run:
                continue
            nodes = configured_voice_services()
            if node_index >= len(nodes):
                VOICE_QUEUE.put(task)
                time.sleep(1)
                continue
            with VOICE_NODE_LOCK:
                VOICE_NODE_JOBS[node_index] = str(task[0])
            voice_stage(*task, nodes[node_index], node_index)
        except Exception as exc:
            job_id = str(task[0])
            if job_id in JOBS:
                fail_job(job_id, "语音队列异常", exc)
        finally:
            with VOICE_NODE_LOCK:
                VOICE_NODE_JOBS[node_index] = None
            VOICE_QUEUE.task_done()


def regenerate_board_image(job_id: str, page: int, prompt: str) -> None:
    """Regenerate one existing board while keeping the previous revision recoverable."""
    job_dir = JOBS_DIR / job_id
    try:
        with LOCK:
            selected = JOBS[job_id].copy()
            source_id = job_id
            source = selected
            visited = {job_id}
            while source.get("job_type") == "rerender" and source.get("rerender_of"):
                candidate = str(source["rerender_of"])
                if candidate in visited or candidate not in JOBS:
                    break
                visited.add(candidate)
                source_id = candidate
                source = JOBS[candidate].copy()
        plan_path = job_dir / "plan.json"
        boards_path = job_dir / "boards.json"
        scenes = json.loads(plan_path.read_text(encoding="utf-8"))
        if not isinstance(scenes, list) or not scenes:
            raise RuntimeError("任务的页面结构文件无效")
        scenes_per_image = 1 if is_infographic_job(job_id) else max(1, min(4, int(selected.get("scenes_per_image", source.get("scenes_per_image", 1)))))
        boards = [scenes[index:index + scenes_per_image] for index in range(0, len(scenes), scenes_per_image)]
        if page < 1 or page > len(boards):
            raise RuntimeError(f"第 {page} 张图片不存在")

        begin_phase(job_id, "images", "单图重生成", f"正在按修改后的提示词重新生成第 {page} 张图片", 50)
        config = model_config_for_job(job_id)
        board = boards[page - 1]
        reference_images, _reference_instruction, _character_context = visual_reference_context(source_id)
        style = str(source.get("style") or DEFAULT_STYLE)
        if style == PAPER_METAPHOR_STYLE and not reference_images:
            reference_images, _reference_instruction = paper_metaphor_reference_context(board)
        elif style == OIL_VISUAL_STYLE and not reference_images:
            reference_images, _reference_instruction = oil_visual_reference_context(board, is_infographic_job(job_id))

        stem = f"board-{page:02d}"
        image = job_dir / f"{stem}.png"
        source_image = job_dir / f"{stem}.source.png"
        line_image = job_dir / f"{stem}.line.png"
        partial_image = job_dir / f"{stem}.source.partial.png"
        last_error: Exception | None = None
        for attempt in range(3):
            partial_image.unlink(missing_ok=True)
            try:
                generate_image(config, prompt, partial_image, reference_images, job_id)
                ensure_job_active(job_id)
                if valid_image_file(partial_image):
                    break
                raise RuntimeError("模型返回的图片文件无效")
            except JobCancelled:
                raise
            except ProviderHTTPError:
                raise
            except (RuntimeError, ValueError, OSError) as exc:
                last_error = exc
                if attempt == 2:
                    break
                update_job(job_id, stage=f"第 {page} 张图片结果异常，正在自动重试 {attempt + 2}/3")
                time.sleep(provider_retry_delay(attempt))
        if not valid_image_file(partial_image):
            raise RuntimeError(f"第 {page} 张图片连续 3 次生成无效：{last_error}")
        partial_line = job_dir / f"{stem}.line.partial.png"
        if style == MEMORY_HANDDRAW_STYLE:
            partial_line.unlink(missing_ok=True)
            ensure_memory_lineart_asset(style, partial_image, partial_line)

        revision_dir = job_dir / "revisions" / time.strftime("%Y%m%d-%H%M%S")
        revision_dir.mkdir(parents=True, exist_ok=True)
        for previous in (source_image, line_image, image, boards_path):
            if previous.exists():
                shutil.copy2(previous, revision_dir / previous.name)
        partial_image.replace(source_image)
        if style == MEMORY_HANDDRAW_STYLE:
            partial_line.replace(line_image)
        include_key_text = bool(selected.get("include_key_text", source.get("include_key_text", True)))
        if style == MEMORY_HANDDRAW_STYLE:
            shutil.copy2(source_image, image)
        elif include_key_text and not is_infographic_job(job_id):
            from scripts.add_key_text import add_key_text
            add_key_text(source_image, [str(scene.get("key_text", "")) for scene in board], image)
        else:
            shutil.copy2(source_image, image)

        try:
            manifest = json.loads(boards_path.read_text(encoding="utf-8")) if boards_path.exists() else []
        except (OSError, json.JSONDecodeError):
            manifest = []
        if not isinstance(manifest, list):
            manifest = []
        while len(manifest) < len(boards):
            index = len(manifest)
            manifest.append({
                "scene_numbers": list(range(index * scenes_per_image + 1, index * scenes_per_image + len(boards[index]) + 1)),
                "image_prompt": "",
            })
        manifest[page - 1]["image_prompt"] = prompt
        if style == MEMORY_HANDDRAW_STYLE:
            manifest[page - 1]["color_image"] = source_image.name
            manifest[page - 1]["line_image"] = line_image.name
        atomic_write_json(boards_path, manifest)
        finish_timing(job_id)
        update_job(
            job_id,
            status="done",
            stage=f"第 {page} 张图片已重新生成，可重新渲染成片",
            progress=100,
            completed_boards=len([path for path in job_dir.glob("board-*.png") if re.fullmatch(r"board-\d+\.png", path.name)]),
            board_regeneration=None,
            error=None,
            can_rerender=True,
        )
    except Exception as exc:
        fail_job(job_id, "单图重新生成失败", exc)


def model_queue_worker() -> None:
    while True:
        task = MODEL_QUEUE.get()
        try:
            command = str(task[0])
            job_id = str(task[1]) if command == "regenerate_board" else command
            with LOCK:
                should_run = JOBS.get(job_id, {}).get("status") in {"queued", "running"}
            if not should_run:
                continue
            if command == "regenerate_board":
                regenerate_board_image(job_id, int(task[2]), str(task[3]))
            else:
                model_stage(*task)
        except Exception as exc:
            command = str(task[0])
            job_id = str(task[1]) if command == "regenerate_board" else command
            if job_id in JOBS:
                fail_job(job_id, "模型队列异常", exc)
        finally:
            MODEL_QUEUE.task_done()


def start_render_task(target: Any, *args: Any) -> None:
    job_id = str(args[0])

    def runner() -> None:
        try:
            with LOCK:
                should_run = JOBS.get(job_id, {}).get("status") in {"queued", "running"}
            if not should_run:
                return
            target(*args)
        finally:
            with RENDER_THREADS_LOCK:
                RENDER_THREADS.discard(threading.current_thread())

    thread = threading.Thread(target=runner, name=f"local-render-{job_id}", daemon=True)
    with RENDER_THREADS_LOCK:
        RENDER_THREADS.add(thread)
    thread.start()


def ensure_pipeline_workers() -> None:
    with WORKER_LOCK:
        for index, _url in enumerate(configured_voice_services()):
            thread = VOICE_WORKER_THREADS.get(index)
            if thread is None or not thread.is_alive():
                thread = threading.Thread(target=voice_queue_worker, args=(index,), name=f"voice-worker-{index + 1}", daemon=True)
                VOICE_WORKER_THREADS[index] = thread
                thread.start()
        MODEL_WORKER_THREADS[:] = [thread for thread in MODEL_WORKER_THREADS if thread.is_alive()]
        while len(MODEL_WORKER_THREADS) < MODEL_CONCURRENCY:
            index = len(MODEL_WORKER_THREADS) + 1
            thread = threading.Thread(target=model_queue_worker, name=f"model-worker-{index}", daemon=True)
            MODEL_WORKER_THREADS.append(thread)
            thread.start()


def enqueue_job_from_checkpoint(job_id: str, item: dict[str, Any]) -> None:
    job_dir = JOBS_DIR / job_id
    pending_regeneration = item.get("board_regeneration")
    if isinstance(pending_regeneration, dict):
        page = int(pending_regeneration.get("page", 0))
        prompt = str(pending_regeneration.get("prompt") or "").strip()
        if page < 1 or not prompt:
            raise RuntimeError("待恢复的单图重生成参数无效")
        queue_for_stage(job_id, "model", f"正在恢复第 {page} 张图片重生成", max(1, int(item.get("progress", 1))))
        MODEL_QUEUE.put(("regenerate_board", job_id, page, prompt))
        return
    result_name = str(item.get("result_file") or "final.mp4")
    if result_name not in VIDEO_RESULT_FILES:
        result_name = "final.mp4"
    if valid_media_file(job_dir / result_name):
        finish_timing(job_id)
        update_job(
            job_id,
            status="done",
            stage="已从断点恢复完成",
            progress=100,
            result_url=f"/api/jobs/{job_id}/download",
            result_file=result_name,
            can_rerender=True,
        )
        return
    scenes_per_image = max(1, min(4, int(item.get("scenes_per_image", 1))))
    pen_text = str(item.get("pen_text", "")).strip()[:12]
    include_key_text = bool(item.get("include_key_text", True))
    include_subtitles = bool(item.get("include_subtitles", True))
    stroke_detail = str(item.get("stroke_detail", "detailed"))
    stroke_detail = stroke_detail if stroke_detail in {"light", "standard", "detailed", "full"} else "detailed"
    if item.get("job_type") == "rerender":
        queue_for_stage(job_id, "render", "正在恢复本地渲染", max(1, int(item.get("progress", 1))))
        if is_infographic_job(job_id):
            scenes = json.loads((job_dir / "plan.json").read_text(encoding="utf-8"))
            duration = probe_duration(job_dir / "voice.wav")
            boards = [[scene] for scene in scenes]
            start_render_task(render_generated_job, job_id, scenes, boards, pen_text, include_subtitles, stroke_detail, duration)
        else:
            start_render_task(rerender_job, job_id, scenes_per_image, pen_text, include_key_text, include_subtitles, stroke_detail)
        return
    copy = str(item.get("copy", "")).strip()
    if not copy:
        raise RuntimeError("旧任务缺少可恢复的文案，请从历史记录重新提交")
    reference = next(iter(sorted(job_dir.glob("reference.*"))), job_dir / "reference.wav")
    task = (job_id, copy, str(item.get("style", DEFAULT_STYLE)), reference, scenes_per_image, pen_text, include_key_text, include_subtitles, stroke_detail)
    if valid_media_file(job_dir / "voice.wav"):
        queue_for_stage(job_id, "model", "已恢复配音，等待继续模型任务", max(14, int(item.get("progress", 14))))
        MODEL_QUEUE.put(task)
    else:
        if not reference.exists():
            raise RuntimeError("任务缺少参考音频，无法从断点继续")
        queue_for_stage(job_id, "voice", "等待恢复语音克隆", max(1, int(item.get("progress", 1))))
        VOICE_QUEUE.put(task)


def resume_pending_jobs() -> None:
    with LOCK:
        pending = sorted(
            [(job_id, item.copy()) for job_id, item in JOBS.items() if item.get("status") in {"queued", "running"}],
            key=lambda entry: (int(entry[1].get("queue_order", 0)), float(entry[1].get("created_at", 0))),
        )
    for job_id, item in pending:
        try:
            enqueue_job_from_checkpoint(job_id, item)
        except Exception as exc:
            fail_job(job_id, "任务恢复失败", exc)
    ensure_pipeline_workers()


restore_jobs()
resume_pending_jobs()


@app.get("/api/health")
def health() -> dict[str, Any]:
    with RENDER_THREADS_LOCK:
        render_active = sum(1 for thread in RENDER_THREADS if thread.is_alive())
    nodes = configured_voice_services()
    with VOICE_NODE_LOCK:
        voice_nodes = [
            {"index": index + 1, "url": url, "active": bool(VOICE_NODE_JOBS.get(index)), "job_id": VOICE_NODE_JOBS.get(index)}
            for index, url in enumerate(nodes)
        ]
    return {
        "status": "ok",
        "pipeline_version": PIPELINE_VERSION,
        "renderer": PYTHON.exists(),
        "voice_provider": "MiniMax",
        "tts": nodes,
        "queues": {
            "voice": {"concurrency": len(nodes), "waiting": VOICE_QUEUE.qsize(), "nodes": voice_nodes},
            "model": {"concurrency": MODEL_CONCURRENCY, "waiting": MODEL_QUEUE.qsize()},
            "render": {"concurrency": "local-direct", "active": render_active},
        },
    }


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return safe_config(load_config())


@app.post("/api/config")
def save_config(payload: dict[str, Any]) -> dict[str, Any]:
    current = load_config()
    for key in DEFAULT_CONFIG:
        value = payload.get(key)
        if key == "api_key" and isinstance(value, str) and "••••" in value:
            continue
        if key == "gemini_api_key" and isinstance(value, str) and value == "********":
            continue
        if key == "rightcode_api_key" and isinstance(value, str) and value == "********":
            continue
        if key == "minimax_api_key" and isinstance(value, str) and value == "********":
            continue
        if value not in (None, ""):
            current[key] = value.strip() if isinstance(value, str) else value
    current["ai_provider"] = selected_ai_provider(current)
    STATE_DIR.mkdir(exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    ensure_pipeline_workers()
    return safe_config(current)


@app.post("/api/config/test")
def test_config(payload: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    for key, value in payload.items():
        if key not in DEFAULT_CONFIG:
            continue
        if key == "api_key" and isinstance(value, str) and "••••" in value:
            continue
        if key == "gemini_api_key" and isinstance(value, str) and value == "********":
            continue
        if key == "rightcode_api_key" and isinstance(value, str) and value == "********":
            continue
        if key == "minimax_api_key" and isinstance(value, str) and value == "********":
            continue
        if value:
            config[key] = value.strip() if isinstance(value, str) else value
    if selected_ai_provider(config) == "rightcode":
        results: dict[str, Any] = {}
        try:
            text_result = rightcode_generate_text(
                str(config.get("rightcode_api_key", "")),
                str(config.get("rightcode_text_base_url", "")),
                str(config.get("rightcode_text_model", "")),
                "只回复：连接成功",
                timeout=60,
            )
            if not text_result.strip():
                raise RuntimeError("Right Code 文本模型未返回内容")
            results["provider"] = {
                "ok": True,
                "message": f"Right Code {config['rightcode_text_model']} 连接成功",
            }
        except Exception as exc:
            results["provider"] = {"ok": False, "message": str(exc)}
        try:
            models = rightcode_list_models(
                str(config.get("rightcode_api_key", "")),
                str(config.get("rightcode_image_base_url", "")),
            )
            image_model = str(config.get("rightcode_image_model", ""))
            if models and image_model not in models:
                raise RuntimeError(f"当前 Right Code Key 的画图模型列表中没有 {image_model}")
            results["image"] = {
                "ok": True,
                "message": f"Right Code {image_model} 已可用（未生成计费图片）",
            }
        except RightCodeHTTPError as exc:
            if exc.status_code in {404, 405}:
                results["image"] = {
                    "ok": True,
                    "message": f"Right Code {config['rightcode_image_model']} 将在首次生成时验证（未生成计费图片）",
                }
            else:
                results["image"] = {"ok": False, "message": str(exc)}
        except Exception as exc:
            results["image"] = {"ok": False, "message": str(exc)}
        try:
            base_url = str(config.get("minimax_base_url", "")).strip().rstrip("/")
            if not base_url:
                raise RuntimeError("MiniMax API 地址不能为空")
            with httpx.Client(timeout=20) as client:
                response = client.post(
                    f"{base_url}/v1/get_voice",
                    headers=minimax_headers(config),
                    json={"voice_type": "all"},
                )
            minimax_json(response, "voice list")
            results["tts"] = {
                "ok": True,
                "message": "MiniMax 鉴权与音色接口连接成功（未发起计费克隆）",
            }
        except Exception as exc:
            results["tts"] = {"ok": False, "message": str(exc)}
        return results
    if selected_ai_provider(config) == "gemini":
        results: dict[str, Any] = {}
        try:
            text_result = gemini_generate_text(
                str(config.get("gemini_api_key", "")),
                str(config.get("gemini_text_model", "")),
                '只返回 {"status":"ok"}',
                {
                    "type": "object",
                    "properties": {"status": {"type": "string"}},
                    "required": ["status"],
                    "additionalProperties": False,
                },
                timeout=60,
            )
            parsed_result = json.loads(text_result)
            if not isinstance(parsed_result, dict) or not parsed_result.get("status"):
                raise RuntimeError("Gemini 文本模型未返回预期的结构化结果")
            results["provider"] = {
                "ok": True,
                "message": f"Gemini {config['gemini_text_model']} 连接成功",
            }
        except Exception as exc:
            results["provider"] = {"ok": False, "message": str(exc)}
        try:
            gemini_get_model(
                str(config.get("gemini_api_key", "")),
                str(config.get("gemini_image_model", "")),
            )
            results["image"] = {
                "ok": True,
                "message": f"Gemini {config['gemini_image_model']} 已可用（未生成计费图片）",
            }
        except Exception as exc:
            results["image"] = {"ok": False, "message": str(exc)}
        try:
            base_url = str(config.get("minimax_base_url", "")).strip().rstrip("/")
            if not base_url:
                raise RuntimeError("MiniMax API 地址不能为空")
            with httpx.Client(timeout=20) as client:
                response = client.post(
                    f"{base_url}/v1/get_voice",
                    headers=minimax_headers(config),
                    json={"voice_type": "all"},
                )
            minimax_json(response, "voice list")
            results["tts"] = {
                "ok": True,
                "message": "MiniMax 鉴权与音色接口连接成功（未发起计费克隆）",
            }
        except Exception as exc:
            results["tts"] = {"ok": False, "message": str(exc)}
        return results
    results: dict[str, Any] = {}
    try:
        provider_post(config, "responses", {"model": config["text_model"], "input": "只回复：连接成功"}, timeout=60)
        results["openlux"] = {"ok": True, "message": f"OpenLux {config['text_model']} 连接成功"}
    except Exception as exc:
        results["openlux"] = {"ok": False, "message": str(exc)}
    try:
        models = provider_models(config)
        image_model = str(config["image_model"])
        if models and image_model not in models:
            raise RuntimeError(f"当前 Key 的模型列表中没有 {image_model}")
        results["image"] = {"ok": True, "message": f"{image_model} 已可用" if models else f"{image_model} 将在生成时验证"}
    except Exception as exc:
        results["image"] = {"ok": False, "message": str(exc)}
    try:
        base_url = str(config.get("minimax_base_url", "")).strip().rstrip("/")
        if not base_url:
            raise RuntimeError("MiniMax API 地址不能为空")
        with httpx.Client(timeout=20) as client:
            response = client.post(
                f"{base_url}/v1/get_voice",
                headers=minimax_headers(config),
                json={"voice_type": "all"},
            )
        minimax_json(response, "voice list")
        results["tts"] = {
            "ok": True,
            "message": "MiniMax 鉴权与音色接口连接成功（未发起计费克隆）",
        }
    except Exception as exc:
        results["tts"] = {"ok": False, "message": str(exc)}
    return results

@app.get("/api/preferences")
def get_preferences() -> dict[str, Any]:
    if not PREFERENCES_PATH.exists():
        return {"pen_text": "", "stroke_detail": "detailed"}
    try:
        data = json.loads(PREFERENCES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"pen_text": "", "stroke_detail": "detailed"}
    detail = str(data.get("stroke_detail", "detailed"))
    return {"pen_text": str(data.get("pen_text", ""))[:12], "stroke_detail": detail if detail in {"light", "standard", "detailed", "full"} else "detailed"}


@app.post("/api/preferences")
def save_preferences(payload: dict[str, Any]) -> dict[str, Any]:
    detail = str(payload.get("stroke_detail", "detailed"))
    preferences = {
        "pen_text": str(payload.get("pen_text", "")).strip()[:12],
        "stroke_detail": detail if detail in {"light", "standard", "detailed", "full"} else "detailed",
    }
    STATE_DIR.mkdir(exist_ok=True)
    PREFERENCES_PATH.write_text(json.dumps(preferences, ensure_ascii=False, indent=2), encoding="utf-8")
    return preferences


@app.post("/api/jobs")
async def create_job(
    request: Request,
    script: str = Form(..., alias="copy"),
    style: str = Form("极简粗线简笔白板风"),
    scenes_per_image: int = Form(1),
    task_name: str = Form(""),
    pen_text: str = Form(""),
    include_key_text: bool = Form(True),
    include_subtitles: bool = Form(True),
    stroke_detail: str = Form("detailed"),
    reference: UploadFile = File(...),
    reference_mode: str = Form("standard"),
    character_manifest: str = Form("[]"),
    style_reference: UploadFile | None = File(None),
    character_references: list[UploadFile] | None = File(None),
) -> dict[str, Any]:
    if len(script.strip()) < 10:
        raise HTTPException(400, "文案至少需要 10 个字")
    if style == MEMORY_HANDDRAW_STYLE:
        if reference_mode != "standard":
            raise HTTPException(400, "岁月回忆手绘风仅支持标准制作模式")
        scenes_per_image = 1
        if not MEMORY_HANDDRAW_PIPELINE_ENABLED:
            raise HTTPException(409, "岁月回忆手绘风正在完成线稿与渐进上色渲染模块，暂不可提交任务")
    with LOCK:
        pending = sum(1 for item in JOBS.values() if item.get("status") in {"queued", "running"})
    if pending >= MAX_ACTIVE_AND_QUEUED:
        raise HTTPException(429, f"当前已有 {pending} 个任务，请稍后再提交")
    job_id = uuid.uuid4().hex[:12]
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    suffix = (Path(reference.filename or "reference.wav").suffix or ".wav").lower()
    if suffix not in {".mp3", ".m4a", ".wav"}:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(400, "MiniMax 克隆音频只支持 MP3、M4A 或 WAV")
    reference_path = job_dir / f"reference{suffix}"
    with reference_path.open("wb") as target:
        shutil.copyfileobj(reference.file, target)
    if reference_path.stat().st_size > 20 * 1024 * 1024:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(400, "MiniMax 克隆音频不能超过 20 MB")
    if Path(FFPROBE).exists() or shutil.which(FFPROBE):
        try:
            reference_duration = probe_duration(reference_path)
        except (OSError, subprocess.SubprocessError, ValueError):
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(400, "参考音频无效，无法读取时长")
        if not 10 <= reference_duration <= 300:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(400, "MiniMax 克隆音频时长必须在 10 秒至 5 分钟之间")
    reference_mode = reference_mode if reference_mode in {"custom", "infographic"} else "standard"
    visual_references: dict[str, Any] = {}
    if reference_mode == "custom" or character_references or character_manifest.strip() not in {"", "[]"}:
        uploads = character_references or []
        try:
            manifest = json.loads(character_manifest)
        except json.JSONDecodeError as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(400, "人物参考信息格式无效") from exc
        if not isinstance(manifest, list) or not 1 <= len(manifest) <= 5:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(400, "人物参考需要填写 1–5 个人物")
        if reference_mode == "custom" and style_reference is None:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(400, "自定义参考需要上传 1 张风格图")
        try:
            counts = [int(item.get("file_count", 0)) for item in manifest if isinstance(item, dict)]
        except (TypeError, ValueError) as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(400, "人物参考图片数量无效") from exc
        if len(counts) != len(manifest) or any(count < 1 or count > 3 for count in counts):
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(400, "每个人物需要上传 1–3 张参考图")
        expected = sum(counts)
        if expected != len(uploads) or expected < 1 or expected > 15:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(400, "人物参考图片数量不匹配")
        style_path: Path | None = None
        if style_reference is not None:
            style_suffix = Path(style_reference.filename or "style.png").suffix.lower()
            if style_suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                shutil.rmtree(job_dir, ignore_errors=True)
                raise HTTPException(400, "风格参考图只支持 PNG、JPG 或 WebP")
            style_path = job_dir / f"style-reference{style_suffix}"
            with style_path.open("wb") as target:
                shutil.copyfileobj(style_reference.file, target)
        saved_characters: list[dict[str, Any]] = []
        cursor = 0
        for character_index, item in enumerate(manifest, 1):
            if not isinstance(item, dict):
                continue
            count = max(1, min(3, int(item.get("file_count", 1))))
            image_names: list[str] = []
            for image_index, upload in enumerate(uploads[cursor:cursor + count], 1):
                suffix = Path(upload.filename or "character.png").suffix.lower()
                if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                    shutil.rmtree(job_dir, ignore_errors=True)
                    raise HTTPException(400, "人物参考图只支持 PNG、JPG 或 WebP")
                image_name = f"character-{character_index:02d}-{image_index:02d}{suffix}"
                image_path = job_dir / image_name
                with image_path.open("wb") as target:
                    shutil.copyfileobj(upload.file, target)
                if image_path.stat().st_size > 15 * 1024 * 1024 or not valid_image_file(image_path):
                    shutil.rmtree(job_dir, ignore_errors=True)
                    raise HTTPException(400, "人物参考图无效或超过 15MB")
                image_names.append(image_name)
            cursor += count
            saved_characters.append({
                "name": str(item.get("name") or f"人物 {character_index}").strip()[:20],
                "description": str(item.get("description") or "").strip()[:80],
                "images": image_names,
            })
        if style_path is not None and (style_path.stat().st_size > 15 * 1024 * 1024 or not valid_image_file(style_path)):
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(400, "风格参考图无效或超过 15MB")
        visual_references = {"characters": saved_characters}
        if style_path is not None:
            visual_references["style_image"] = style_path.name
    scenes_per_image = max(1, min(4, scenes_per_image))
    stroke_detail = stroke_detail if stroke_detail in {"light", "standard", "detailed", "full"} else "detailed"
    task_name = normalized_task_name(task_name, script, job_id)
    provider_snapshot = provider_job_snapshot(load_config())
    now = time.time()
    with LOCK:
        JOBS[job_id] = {
            "id": job_id, "status": "queued", "stage": "等待 MiniMax 音色克隆", "progress": 1,
            "created_at": now, "started_at": now, "timings": {},
            "queue_stage": "voice", "queue_order": time.time_ns(),
            "client_ip": request_client_ip(request),
            **provider_snapshot,
            "job_type": "infographic" if reference_mode == "infographic" else "generate", "style": style, "scenes_per_image": scenes_per_image,
            "pipeline_version": PIPELINE_VERSION if reference_mode == "infographic" else "standard_v1",
            "reference_mode": reference_mode, "character_count": len(visual_references.get("characters", [])),
            "visual_references": visual_references,
            "task_name": task_name,
            "copy": script.strip(),
            "pen_text": pen_text.strip()[:12], "include_key_text": include_key_text,
            "include_subtitles": include_subtitles,
            "stroke_detail": stroke_detail, "can_rerender": False,
            "minimax_voice_cloned": False,
            "current_phase": None, "phase_started_at": None, "total_elapsed": 0.0,
        }
        _persist_job_locked(job_id)
    VOICE_QUEUE.put((job_id, script.strip(), style, reference_path, scenes_per_image, pen_text.strip()[:12], include_key_text, include_subtitles, stroke_detail))
    ensure_pipeline_workers()
    return job_snapshot(job_id)


@app.get("/api/jobs")
def list_jobs(limit: int = 20) -> dict[str, Any]:
    with LOCK:
        ids = sorted(JOBS, key=lambda item: float(JOBS[item].get("created_at", 0)), reverse=True)[:max(1, min(100, limit))]
    return {"items": [job_snapshot(job_id) for job_id in ids]}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    now = time.time()
    with LOCK:
        if job_id not in JOBS:
            raise HTTPException(404, "任务不存在")
        item = JOBS[job_id]
        if item.get("status") not in {"queued", "running"}:
            raise HTTPException(400, "该任务当前不需要取消")
        current = item.get("current_phase")
        started = item.get("phase_started_at")
        if current and started:
            entry = item.setdefault("timings", {}).setdefault(current, {"label": current, "seconds": 0.0})
            entry["seconds"] = float(entry.get("seconds", 0.0)) + max(0.0, now - float(started))
        item.update(
            status="cancelled",
            stage="任务已取消",
            error=None,
            cancel_requested=True,
            cancelled_at=now,
            finished_at=now,
            current_phase=None,
            phase_started_at=None,
            total_elapsed=max(0.0, now - float(item.get("started_at", now))),
        )
        _persist_job_locked(job_id)
    terminate_running_process(job_id)
    return job_snapshot(job_id)


def parameter_source(job_id: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    with LOCK:
        if job_id not in JOBS:
            raise HTTPException(404, "历史任务不存在")
        selected = JOBS[job_id].copy()
        source_id = job_id
        source = selected
        visited = {job_id}
        while source.get("job_type") == "rerender" and source.get("rerender_of"):
            candidate = str(source["rerender_of"])
            if candidate in visited or candidate not in JOBS:
                break
            visited.add(candidate)
            source_id = candidate
            source = JOBS[candidate].copy()
    return source_id, source, selected


def asset_descriptor(job_id: str, filename: str | None) -> dict[str, str] | None:
    if not filename:
        return None
    path = JOBS_DIR / job_id / filename
    if not path.is_file():
        return None
    return {
        "name": filename,
        "url": f"/api/jobs/{job_id}/assets/{filename}",
        "content_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
    }


@app.get("/api/jobs/{job_id}/parameters")
def get_job_parameters(job_id: str) -> dict[str, Any]:
    source_id, source, selected = parameter_source(job_id)
    source_dir = JOBS_DIR / source_id
    reference = next(iter(sorted(source_dir.glob("reference.*"))), None)
    visual_references = source.get("visual_references") if isinstance(source.get("visual_references"), dict) else {}
    style_filename = str(visual_references.get("style_image") or "")
    characters: list[dict[str, Any]] = []
    for index, raw in enumerate(visual_references.get("characters") or [], 1):
        if not isinstance(raw, dict):
            continue
        images = [
            descriptor
            for name in raw.get("images") or []
            if (descriptor := asset_descriptor(source_id, str(name))) is not None
        ]
        characters.append({
            "name": str(raw.get("name") or f"人物 {index}"),
            "description": str(raw.get("description") or ""),
            "images": images,
        })
    reference_mode = str(source.get("reference_mode") or "standard")
    if reference_mode not in {"standard", "custom", "infographic"}:
        reference_mode = "custom" if visual_references else "standard"
    return {
        "job_id": job_id,
        "source_job_id": source_id,
        "copy": str(source.get("copy") or ""),
        "reference_mode": reference_mode,
        "style": str(source.get("style") or DEFAULT_STYLE),
        "scenes_per_image": max(1, min(4, int(source.get("scenes_per_image", 1)))),
        "task_name": str(selected.get("task_name") or source.get("task_name") or ""),
        "pen_text": str(selected.get("pen_text", source.get("pen_text", ""))),
        "include_key_text": bool(selected.get("include_key_text", source.get("include_key_text", True))),
        "include_subtitles": bool(selected.get("include_subtitles", source.get("include_subtitles", True))),
        "stroke_detail": str(selected.get("stroke_detail", source.get("stroke_detail", "detailed"))),
        "reference": asset_descriptor(source_id, reference.name if reference else None),
        "style_reference": asset_descriptor(source_id, style_filename),
        "characters": characters,
    }


@app.get("/api/jobs/{job_id}/assets/{filename}")
def get_job_input_asset(job_id: str, filename: str) -> FileResponse:
    if Path(filename).name != filename:
        raise HTTPException(404, "素材不存在")
    with LOCK:
        item = JOBS.get(job_id)
        if item is None:
            raise HTTPException(404, "历史任务不存在")
        visual_references = item.get("visual_references") if isinstance(item.get("visual_references"), dict) else {}
    allowed = {path.name for path in (JOBS_DIR / job_id).glob("reference.*")}
    style_filename = str(visual_references.get("style_image") or "")
    if style_filename:
        allowed.add(style_filename)
    for character in visual_references.get("characters") or []:
        if isinstance(character, dict):
            allowed.update(str(name) for name in character.get("images") or [])
    if filename not in allowed:
        raise HTTPException(404, "素材不存在")
    path = JOBS_DIR / job_id / filename
    if not path.is_file():
        raise HTTPException(404, "素材不存在")
    return FileResponse(path, media_type=mimetypes.guess_type(filename)[0] or "application/octet-stream", filename=filename)


@app.get("/api/jobs/{job_id}/gallery")
def get_job_gallery(job_id: str) -> dict[str, Any]:
    if job_id not in JOBS:
        raise HTTPException(404, "历史任务不存在")
    job_dir = JOBS_DIR / job_id
    images = sorted(
        (path for path in job_dir.glob("board-*.png") if re.fullmatch(r"board-\d+\.png", path.name)),
        key=lambda path: int(re.search(r"\d+", path.stem).group()) if re.search(r"\d+", path.stem) else 0,
    )
    try:
        manifest = json.loads((job_dir / "boards.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = []
    if not isinstance(manifest, list):
        manifest = []
    items: list[dict[str, Any]] = []
    for path in images:
        match = re.search(r"\d+", path.stem)
        page = int(match.group()) if match else len(items) + 1
        board = manifest[page - 1] if page <= len(manifest) and isinstance(manifest[page - 1], dict) else {}
        items.append({
            "name": path.name,
            "page": page,
            "url": f"/api/jobs/{job_id}/images/{path.name}?v={path.stat().st_mtime_ns}",
            "size": path.stat().st_size,
            "prompt": str(board.get("image_prompt") or ""),
            "scene_numbers": board.get("scene_numbers") if isinstance(board.get("scene_numbers"), list) else [],
        })
    return {
        "job_id": job_id,
        "items": items,
    }


@app.get("/api/jobs/{job_id}/images/{filename}")
def get_job_generated_image(job_id: str, filename: str) -> FileResponse:
    if job_id not in JOBS or not re.fullmatch(r"board-\d+\.png", filename):
        raise HTTPException(404, "图片不存在")
    path = JOBS_DIR / job_id / filename
    if not valid_image_file(path):
        raise HTTPException(404, "图片不存在或尚未生成")
    return FileResponse(path, media_type="image/png")


@app.post("/api/jobs/{job_id}/boards/{page}/regenerate")
def regenerate_job_board(job_id: str, page: int, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    prompt = str(payload.get("prompt") or "").strip()
    if len(prompt) < 5:
        raise HTTPException(400, "提示词至少需要 5 个字")
    if len(prompt) > 6000:
        raise HTTPException(400, "提示词不能超过 6000 个字")
    job_dir = JOBS_DIR / job_id
    with LOCK:
        if job_id not in JOBS:
            raise HTTPException(404, "历史任务不存在")
        source = JOBS[job_id]
        if source.get("status") in {"queued", "running"}:
            raise HTTPException(409, "当前任务仍在执行，请完成或取消后再重生成图片")
        image = job_dir / f"board-{page:02d}.png"
        if page < 1 or not valid_image_file(image):
            raise HTTPException(404, "要重生成的图片不存在")
        pending = sum(1 for item in JOBS.values() if item.get("status") in {"queued", "running"})
        if pending >= MAX_ACTIVE_AND_QUEUED:
            raise HTTPException(429, f"当前已有 {pending} 个任务，请稍后再试")
        source.update(
            status="queued",
            stage=f"第 {page} 张图片等待重生成",
            progress=45,
            error=None,
            finished_at=None,
            current_phase=None,
            phase_started_at=None,
            queue_stage="model",
            queue_order=time.time_ns(),
            client_ip=request_client_ip(request),
            can_rerender=False,
            board_regeneration={"page": page, "prompt": prompt},
        )
        _persist_job_locked(job_id)
    MODEL_QUEUE.put(("regenerate_board", job_id, page, prompt))
    ensure_pipeline_workers()
    return job_snapshot(job_id)


@app.post("/api/jobs/{job_id}/retry")
def retry_failed_job(job_id: str, request: Request) -> dict[str, Any]:
    with LOCK:
        if job_id not in JOBS:
            raise HTTPException(404, "历史任务不存在")
        source = JOBS[job_id]
        if source.get("status") != "error":
            raise HTTPException(400, "只有失败任务可以继续")
        pending = sum(1 for item in JOBS.values() if item.get("status") in {"queued", "running"})
        if pending >= MAX_ACTIVE_AND_QUEUED:
            raise HTTPException(429, f"当前已有 {pending} 个任务，请稍后再试")
        source.update(
            status="queued", stage="正在检查任务断点", error=None, finished_at=None,
            current_phase=None, phase_started_at=None, queue_order=time.time_ns(),
            client_ip=request_client_ip(request),
            manual_retry_count=int(source.get("manual_retry_count", 0)) + 1,
        )
        item = source.copy()
        _persist_job_locked(job_id)
    try:
        enqueue_job_from_checkpoint(job_id, item)
        ensure_pipeline_workers()
    except Exception as exc:
        fail_job(job_id, "继续任务失败", exc)
    return job_snapshot(job_id)


@app.post("/api/jobs/{job_id}/rerender")
def create_rerender(job_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    if job_id not in JOBS:
        raise HTTPException(404, "历史任务不存在")
    source_dir = JOBS_DIR / job_id
    required = [source_dir / "voice.wav", source_dir / "plan.json"]
    if any(not path.exists() for path in required) or not list(source_dir.glob("board-*.png")):
        raise HTTPException(400, "该任务缺少配音、分镜计划或原图，无法重新渲染")
    with LOCK:
        pending = sum(1 for item in JOBS.values() if item.get("status") in {"queued", "running"})
        source = JOBS[job_id].copy()
    if pending >= MAX_ACTIVE_AND_QUEUED:
        raise HTTPException(429, f"当前已有 {pending} 个任务，请稍后再提交")
    detail = str(payload.get("stroke_detail", source.get("stroke_detail", "detailed")))
    detail = detail if detail in {"light", "standard", "detailed", "full"} else "detailed"
    scenes_per_image = max(1, min(4, int(source.get("scenes_per_image", 1))))
    task_name = normalized_task_name(payload.get("task_name") or source.get("task_name"), str(source.get("copy", "")), job_id)
    pen_text = str(payload.get("pen_text", source.get("pen_text", ""))).strip()[:12]
    include_key_text = bool(payload.get("include_key_text", source.get("include_key_text", True)))
    include_subtitles = bool(payload.get("include_subtitles", source.get("include_subtitles", True)))
    new_id = uuid.uuid4().hex[:12]
    target_dir = JOBS_DIR / new_id
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in ("voice.wav", "plan.json", "boards.json"):
        candidate = source_dir / name
        if candidate.exists():
            shutil.copy2(candidate, target_dir / name)
    for image in source_dir.glob("board-*.png"):
        shutil.copy2(image, target_dir / image.name)
    now = time.time()
    with LOCK:
        JOBS[new_id] = {
            "id": new_id, "status": "queued", "stage": "准备重新渲染", "progress": 1,
            "created_at": now, "started_at": now, "timings": {},
            "queue_stage": "render", "queue_order": time.time_ns(),
            "client_ip": request_client_ip(request),
            "job_type": "rerender", "rerender_of": job_id, "style": source.get("style", ""),
            "reference_mode": source.get("reference_mode", "standard"),
            "pipeline_version": PIPELINE_VERSION if is_infographic_job(job_id) else source.get("pipeline_version", "standard_v1"),
            "task_name": task_name,
            "scenes_per_image": scenes_per_image, "pen_text": pen_text,
            "include_key_text": include_key_text, "include_subtitles": include_subtitles,
            "stroke_detail": detail, "can_rerender": False,
            "current_phase": None, "phase_started_at": None, "total_elapsed": 0.0,
        }
        _persist_job_locked(new_id)
    if is_infographic_job(new_id):
        scenes = json.loads((target_dir / "plan.json").read_text(encoding="utf-8"))
        duration = probe_duration(target_dir / "voice.wav")
        boards = [[scene] for scene in scenes]
        start_render_task(render_generated_job, new_id, scenes, boards, pen_text, include_subtitles, detail, duration)
    else:
        start_render_task(rerender_job, new_id, scenes_per_image, pen_text, include_key_text, include_subtitles, detail)
    return job_snapshot(new_id)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    if job_id not in JOBS:
        raise HTTPException(404, "任务不存在或服务已经重启")
    return job_snapshot(job_id)


@app.get("/api/jobs/{job_id}/download")
def download_job(job_id: str) -> FileResponse:
    item = JOBS.get(job_id)
    if not item:
        raise HTTPException(404, "任务不存在")
    result_name = str(item.get("result_file") or "final.mp4")
    if result_name not in VIDEO_RESULT_FILES:
        raise HTTPException(404, "视频文件记录无效")
    path = JOBS_DIR / job_id / result_name
    if not path.exists():
        raise HTTPException(404, "视频尚未生成")
    return FileResponse(path, media_type="video/mp4", filename=f"whiteboard-{job_id}.mp4")
