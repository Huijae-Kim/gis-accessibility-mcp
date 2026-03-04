"""
gis_analysis_v7.py

Changes from gis_analysis_v6.py:
- [CRITICAL] Strengthened confirmation guard to prevent Claude from bypassing the
  wizard flow and running analysis immediately without user confirmation:
  1. run() docstring now contains explicit HARD STOP instructions so Claude
     never calls run(confirm=True) without user approval.
  2. run() method parameter default changed from "2SFCA" to None — Claude is
     forced to go through wizard_build_job to pick a metric, not guess.
  3. run() now rejects calls where method is None (missing parameter error).
  4. run_job() now always forces confirm=False on the first call and requires
     the user to explicitly re-invoke with confirm=True — it no longer blindly
     forwards the confirm flag from the job JSON.
  5. wizard_build_job() docstring reinforced to instruct Claude to ALWAYS show
     the PARAM_CONFIRM_REQUIRED payload to the user and wait for explicit
     approval before calling finalize=True.
  6. _format_confirm_prompt() output now explicitly tells Claude (in ALL-CAPS)
     not to call run(confirm=True) until the user types a confirmation message.

Changes from gis_analysis_v3.py (inherited from v4-v6):
- load_data now returns shapefiles as outputs:
  - shp_unit: the analysis unit boundary (e.g., 읍면동/동) after join/filter
  - shp_boundary (optional): higher-level boundary (e.g., 시군구) for visualization
- Unified distance decay/weight concept under `distance_decay_function` across the codebase.
  - Backward-compatible alias: `decay_function` is still accepted but deprecated.
- Fixed bbox cropping bug: visualization/overlay no longer uses `threshold_km` to expand bounds.
  Instead, bounds are padded by 10% in both x/y directions (CRS unit = meters in EPSG:5179).
- For distance_decay_function='step', `distance_bands_json` is now enforced and applied
  consistently to both GRAVITY and E2SFCA.
- load_data now returns shapefiles as outputs:
  - shp_unit: the analysis unit boundary (e.g., 읍면동/동) after join/filter
  - shp_boundary (optional): higher-level boundary (e.g., 시군구) for visualization
- Unified distance decay/weight concept under `distance_decay_function` across the codebase.
  - Backward-compatible alias: `decay_function` is still accepted but deprecated.
- Fixed bbox cropping bug: visualization/overlay no longer uses `threshold_km` to expand bounds.
  Instead, bounds are padded by 10% in both x/y directions (CRS unit = meters in EPSG:5179).

Supported distance_decay_function values (case-insensitive):
  "binary"      — 1 if d <= threshold, else 0  (default for 2SFCA)
  "step"        — piecewise constant; requires distance_bands_json
  "gaussian"    — exp(-d^2 / (2*beta^2));  requires beta (= bandwidth sigma, meters)
  "exponential" — exp(-beta * d);           requires beta
  "power"       — (d + eps)^(-beta);        requires beta

Supported metrics (method) — unchanged:
- "MIN"      : minimum impedance to the nearest provider (Eq. 1)
- "K_AVG"    : average impedance to the k-nearest providers (Eq. 2)
- "COM"      : cumulative opportunity measure (Eq. 3)
- "GRAVITY"  : gravity-based potential accessibility (Eq. 4)
- "2SFCA"    : Two-Step Floating Catchment Area (Eq. 5-6)
- "E2SFCA"   : Enhanced 2SFCA with within-catchment distance weights (Eq. 7-8)
- "PPR"      : Population-to-provider ratio (simple, commonly used proxy)

Notes
-----
* The equations referenced above follow the preprint
  "A Conceptual Framework for Spatial Accessibility" (Ahn et al., 2026).
* For FCA-family measures, this implementation uses a sparse OD table
  (pairs within threshold) rather than an NxM dense matrix for memory safety.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
import json
import os
import platform
import re

import numpy as np
import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt
from shapely.geometry import Point
from scipy.spatial import cKDTree

import base64 as _b64

from mcp.server.fastmcp import FastMCP
# ============================================================
# Optional: Web UI link for users (e.g., published Claude Artifact)
# Set as env var ACCESS_WIZARD_UI_URL or edit UI_PUBLIC_URL directly.
# ============================================================
DEFAULT_UI_PUBLIC_URL = "https://claude.ai/public/artifacts/05cc9988-9288-4b66-894d-f7e5b04aac52"
UI_PUBLIC_URL = os.environ.get("ACCESS_WIZARD_UI_URL", "").strip() or DEFAULT_UI_PUBLIC_URL


# =============================================================================
# MCP server
# =============================================================================
mcp = FastMCP("Accessibility GIS Analyst")


# =============================================================================
# Plot defaults
# =============================================================================
if platform.system() == "Darwin":
    plt.rcParams["font.family"] = "AppleGothic"
elif platform.system() == "Windows":
    plt.rcParams["font.family"] = "Malgun Gothic"
else:
    plt.rcParams["font.family"] = "NanumGothic"

plt.rcParams["axes.unicode_minus"] = False


# =============================================================================
# File Directory & Name
# =============================================================================
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FILE_DIR = os.environ.get("ACCESS_DATA_DIR", os.path.join(_SCRIPT_DIR, "_data"))
FILE_POP = "인구_전처리.csv"
FILE_SUPPLY = "전국 병의원 현황_전처리.csv"
FILE_SHP_UNIT = "BND_ADM_DONG_PG.shp"
FILE_SHP_BOUNDARY = "BND_SIGUNGU_PG.shp"


# =============================================================================
# Internal standard schema
# =============================================================================
DEMAND_ID   = "demand_id"
DEMAND_NAME = "demand_name"
DEMAND_POP  = "demand_pop"

SUPPLY_ID   = "supply_id"
SUPPLY_NAME = "supply_name"
SUPPLY_CAP  = "supply_cap"

OD_DEMAND = "demand_id"
OD_SUPPLY = "supply_id"
OD_COST   = "cost"


# =============================================================================
# Configuration
# =============================================================================
@dataclass
class DataConfig:
    """
    DataConfig keeps file paths and default CRS assumptions.

    data_dir should contain:
      - population preprocessed csv (file_pop)
      - provider/supply csv (file_supply)
      - analysis unit boundary shapefile (file_shp_unit)
      - optional boundary shapefile for overlay (file_shp_boundary)
    """
    data_dir: str
    file_pop: str = "인구_전처리.csv"
    file_supply: str = "전국 병의원 현황_전처리.csv"
    file_shp_unit: str = "BND_ADM_DONG_PG.shp"
    file_shp_boundary: Optional[str] = "BND_SIGUNGU_PG.shp"

    crs_supply_xy: str = "EPSG:4326"
    crs_analysis: str  = "EPSG:5179"


@dataclass
class ODConfig:
    """
    ODConfig determines how to build/load impedance (OD) data.

    mode:
      - "euclidean_within"    : sparse OD pairs within max_cost (meters)
      - "euclidean_k_nearest" : k-nearest pairs via KDTree
      - "precomputed_csv"     : load from CSV (demand_id, supply_id, cost)
    """
    mode: str
    max_cost: Optional[float] = None
    k: Optional[int] = None
    od_csv_path: Optional[str] = None
    od_cols: Tuple[str, str, str] = (OD_DEMAND, OD_SUPPLY, OD_COST)


# =============================================================================
# Utilities
# =============================================================================
def _normalize_method(method: str) -> str:
    if not method:
        return ""
    m = method.strip().upper()
    m = m.replace(" ", "").replace("-", "").replace("_", "")
    aliases = {
        "NEAREST": "MIN",
        "MINIMPEDANCE": "MIN",
        "MINIMUMIMPEDANCE": "MIN",
        "KNEAREST": "K_AVG",
        "KNN": "K_AVG",
        "KAVG": "K_AVG",
        "CUMULATIVE": "COM",
        "CUMULATIVEOPPORTUNITY": "COM",
        "OPPORTUNITY": "COM",
        "GRAVITYBASED": "GRAVITY",
        "2STEPFCA": "2SFCA",
        "TWOSTEPFCA": "2SFCA",
        "E2SFCA": "E2SFCA",
        "ENHANCED2SFCA": "E2SFCA",
        "PPR": "PPR",
    }
    return aliases.get(m, m)


def _normalize_decay_function(df: Optional[str]) -> str:
    """Normalize decay_function string to a canonical lowercase key."""
    if not df:
        return "binary"
    return str(df).strip().lower()


def _first_existing(columns: Iterable[str], candidates: List[str]) -> Optional[str]:
    cols = set(columns)
    for c in candidates:
        if c in cols:
            return c
    return None


def _ensure_crs(gdf: gpd.GeoDataFrame, crs: str) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        return gdf.set_crs(crs)
    return gdf.to_crs(crs)


def _representative_points(gdf: gpd.GeoDataFrame) -> gpd.GeoSeries:
    return gdf.geometry.representative_point()


def _safe_div(numer: pd.Series, denom: pd.Series) -> pd.Series:
    out = numer / denom.replace({0: np.nan})
    return out.fillna(0)



def _expand_bounds(bounds: Iterable[float], pad_ratio: float = 0.10) -> Tuple[float, float, float, float]:
    """Expand (xmin, ymin, xmax, ymax) by pad_ratio in x/y directions."""
    xmin, ymin, xmax, ymax = [float(x) for x in bounds]
    w = xmax - xmin
    h = ymax - ymin
    # fallback for degenerate bounds
    if w == 0:
        w = max(1.0, abs(xmin) * 0.01)
    if h == 0:
        h = max(1.0, abs(ymin) * 0.01)
    pad_x = w * float(pad_ratio)
    pad_y = h * float(pad_ratio)
    return xmin - pad_x, ymin - pad_y, xmax + pad_x, ymax + pad_y


def _parse_json_maybe(s: Optional[Any]) -> Optional[Any]:
    if s is None:
        return None
    if isinstance(s, (dict, list)):
        return s
    s = str(s).strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 파싱 실패: {e}\n입력값: {s}") from e

# =============================================================================
# Region filter helpers
# =============================================================================
# `region_filter` is applied as a substring match on the demand-name column.
# However, macro-region keywords (e.g., '전라권') may not literally appear in
# city/province names (e.g., '광주광역시' does not contain '전라').
# To make chat-based usage robust, we expand common macro-region aliases into
# a list of substrings and apply an OR-filter.
#
# Examples:
#   - '전라권' / '호남' / '전라'  -> ['전라', '광주']
#   - '경상권' / '영남' / '경상'  -> ['경상', '부산', '대구', '울산']
#   - '충청권' / '충청'          -> ['충청', '대전', '세종']
#
_REGION_GROUPS: Dict[str, List[str]] = {
    "수도권": ["서울", "인천", "경기"],
    "경상권": ["경상", "부산", "대구", "울산"],
    "전라권": ["전라", "광주"],
    "충청권": ["충청", "대전", "세종"],
    "강원권": ["강원"],
    "제주권": ["제주"],
}

_REGION_ALIASES: Dict[str, str] = {
    "호남": "전라권",
    "호남권": "전라권",
    "전라": "전라권",
    "영남": "경상권",
    "영남권": "경상권",
    "경상": "경상권",
    "충청": "충청권",
    "수도": "수도권",
    "수도권": "수도권",
    "강원": "강원권",
    "제주": "제주권",
}

def _expand_region_filter_terms(region_filter: Optional[str]) -> Optional[List[str]]:
    if region_filter is None:
        return None
    s = str(region_filter).strip()
    if not s or s.upper() == "ALL" or s in {"전체", "전국"}:
        return None

    # Allow explicit multi-term filters: "전라|광주" or "전라,광주"
    if "|" in s:
        terms = [t.strip() for t in s.split("|") if t.strip()]
        return terms or None
    if "," in s:
        terms = [t.strip() for t in s.split(",") if t.strip()]
        return terms or None

    # Normalize aliases / macro regions
    key = _REGION_ALIASES.get(s, s)
    if key.endswith("권") and key in _REGION_GROUPS:
        return _REGION_GROUPS[key]

    # Fallback: use the raw substring
    return [s]

def _filter_by_region_terms(
    gdf: gpd.GeoDataFrame,
    name_col: str,
    region_filter: Optional[str],
) -> Tuple[gpd.GeoDataFrame, Optional[List[str]]]:
    terms = _expand_region_filter_terms(region_filter)
    if not terms:
        return gdf, None
    pattern = "|".join(re.escape(t) for t in terms if t)
    if not pattern:
        return gdf, None
    out = gdf[gdf[name_col].astype(str).str.contains(pattern, na=False, regex=True)].copy()
    return out, terms

# =============================================================================
# Execution guard (chat-wizard friendly)
# =============================================================================
# By default, this server requires an explicit confirmation step before running
# a potentially expensive analysis. This prevents the chat agent from silently
# picking a metric/parameters and executing immediately.
#
# Usage:
#   - First call run(..., confirm=False)  -> returns a "plan" + asks for confirmation
#   - Then call  run(..., confirm=True)   -> actually executes
#
_METHOD_ALTERNATIVES: Dict[str, List[str]] = {
    "MIN": ["MIN", "K_AVG"],
    "K_AVG": ["MIN", "K_AVG"],
    "COM": ["COM", "GRAVITY"],
    "GRAVITY": ["GRAVITY", "COM"],
    "2SFCA": ["2SFCA", "E2SFCA", "PPR"],
    "E2SFCA": ["E2SFCA", "2SFCA", "PPR"],
    "PPR": ["PPR", "2SFCA", "E2SFCA"],
}

def _format_confirm_prompt(
    *,
    method: str,
    target_subject: Optional[str],
    region_filter: Optional[str],
    threshold_km: Optional[float],
    k: Optional[int],
    distance_decay_function: Optional[str],
    beta: Optional[float],
    distance_bands_json: Optional[str],
    ratio_type: Optional[str],
) -> str:
    """Return a chat-friendly confirmation prompt.

    This prompt is designed to *force* the agent to stop and ask the user to confirm
    not only the metric(method) but also the key parameters (threshold/decay/beta/...).
    """
    m = _normalize_method(method)
    alts = _METHOD_ALTERNATIVES.get(m, [m])

    # Region expansion hint (macro-region -> multiple substrings)
    region_terms = _expand_region_filter_terms(region_filter) if region_filter else None

    # Parameter checklist (explicit questions)
    questions: List[str] = []
    # Impedance note (this server currently uses euclidean distance in meters)
    if m in {"MIN", "K_AVG"}:
        questions.append("임피던스는 **직선거리(Euclidean, meters)** 기준으로 계산됩니다. 괜찮을까요? (시간/도로망은 현재 미지원)")

    if m in {"COM", "2SFCA", "E2SFCA", "PPR", "GRAVITY"}:
        questions.append(f"catchment/최대반경(threshold_km)을 **{threshold_km} km**로 둘까요? "
                        f"(보통 5~20km 범위를 검토합니다)")

    if m == "K_AVG":
        questions.append(f"k(고려할 시설 수)을 **{k}**로 둘까요? (일반적으로 3~10)")

    if m in {"GRAVITY", "E2SFCA"}:
        df = distance_decay_function or "(미지정)"
        questions.append(f"distance_decay_function을 **{df}**로 둘까요? "
                        f"(가능: binary/step/gaussian/exponential/power)")
        if df and _normalize_decay_function(df) == "step":
            questions.append("step 가중치 구간(distance_bands_json)이 맞는지 확인해 주세요. (단위: m)")
        if df and _normalize_decay_function(df) in {"gaussian", "exponential", "power"}:
            questions.append(f"beta를 **{beta}**로 둘까요? (함수에 따라 단위/의미가 다릅니다)")
            # Warn about exponential unit mismatch if user uses 1/km convention
            if _normalize_decay_function(df) == "exponential" and beta is not None:
                questions.append("⚠️ exponential의 beta는 이 서버에서 **1/m**로 해석됩니다. "
                                "만약 1/km 기준(예: 0.1)으로 생각했다면, **0.0001(=0.1/1000)**로 변환해야 합니다. "
                                "Wizard(논문 UI)로 만든 설정은 자동 변환됩니다.")

    if m == "PPR" and ratio_type:
        questions.append(f"PPR ratio_type을 **{ratio_type}**로 둘까요?")

    payload = {
        "status": "CONFIRM_REQUIRED",
        "current": {
            "method": m,
            "region_filter": region_filter,
            "region_filter_terms": region_terms,
            "target_subject": target_subject,
            "threshold_km": threshold_km,
            "k": k,
            "distance_decay_function": distance_decay_function,
            "beta": beta,
            "distance_bands_json": distance_bands_json,
            "ratio_type": ratio_type,
        },
        "alternatives": alts,
        "questions": questions,
        "how_to_proceed": "원하는 지표/파라미터를 확정한 뒤, 동일한 입력으로 run(confirm=true)로 다시 호출하세요.",
        "tip": "추천값으로 바로 실행하려면: '추천 설정으로 진행해줘'라고 말해 주세요.",
    }

    msg_lines = [
        "[AGENT INSTRUCTION — HARD STOP: Do NOT call run(confirm=True) or run_job(). "
        "You MUST show the checklist below to the user verbatim and WAIT for explicit user approval "
        "(e.g. '이대로 진행', '확인', 'yes') before making any further tool calls.]",
        "",
        "⚙️ **분석 실행 전 확인(Confirm) 단계**",
        "",
        f"- 지역: **{region_filter or '(전체)'}**" + (f"  (확장: {', '.join(region_terms)})" if region_terms else ""),
        f"- 대상 서비스(target_subject): **{target_subject or '(미지정)'}**",
        f"- 선택된 지표(method): **{m}**",
    ]
    if threshold_km is not None:
        msg_lines.append(f"- threshold_km: **{threshold_km} km**")
    if k is not None:
        msg_lines.append(f"- k: **{k}**")
    if distance_decay_function is not None:
        msg_lines.append(f"- distance_decay_function: **{distance_decay_function}**")
    if beta is not None:
        msg_lines.append(f"- beta: **{beta}**")
    if distance_bands_json is not None:
        msg_lines.append(f"- distance_bands_json: **(지정됨)**")
    if ratio_type is not None:
        msg_lines.append(f"- ratio_type: **{ratio_type}**")

    if questions:
        msg_lines += ["", "🔎 **아래 항목을 확인해 주세요:**"]
        for q in questions:
            msg_lines.append(f"- {q}")

    msg_lines += [
        "",
        "이 설정으로 **실행할까요?**",
        f"가능한 대안 지표: {', '.join(alts)}",
        "",
        "✅ 실행하려면: **'이대로 진행'** 또는 **'확인'** 이라고 답해 주세요.",
        "🛠️ 바꾸려면: 예) **'K_AVG로 하고 k=5'**, **'E2SFCA + gaussian + beta=7000'**, **'threshold_km=15'**",
        "",
        "[AGENT: Only AFTER the user sends an explicit approval message in chat, "
        "call run(confirm=True) with identical parameters. Never auto-proceed.]",
        "",
        "```json",
        json.dumps(payload, ensure_ascii=False, indent=2),
        "```",
    ]
    return "\n".join(msg_lines)

# =============================================================================
# Weight / decay functions
# =============================================================================
def build_decay_function(params: dict) -> Callable[[np.ndarray], np.ndarray]:
    """
    Returns a vectorized weight function w(d) based on params["distance_decay_function"].

    Supported distance_decay_function values:
      "binary"      — w(d) = 1 if d <= threshold else 0
      "step"        — piecewise constant; requires params["distance_bands"]
                      e.g. [[0, 5000, 1.0], [5000, 10000, 0.7], [10000, 20000, 0.3]]
      "gaussian"    — w(d) = exp(-d^2 / (2 * beta^2))
                      beta = bandwidth sigma (same unit as d, i.e. meters)
                      Optional threshold cutoff: w(d) = 0 if d > threshold
      "exponential" — w(d) = exp(-beta * d); requires params["beta"]
                      Optional threshold cutoff: w(d) = 0 if d > threshold
      "power"       — w(d) = (d + eps)^(-beta); requires params["beta"]
                      Optional threshold cutoff: w(d) = 0 if d > threshold

    Common params:
      distance_decay_function : str  (see above)
      threshold      : float (optional hard cutoff, same unit as d)
      beta           : float (required for gaussian / exponential / power)
      distance_bands : list of [lo, hi, w] (required for step)
      eps            : float (for power, default 1e-6)
    """
    decay_fn = _normalize_decay_function(params.get("distance_decay_function") or params.get("decay_function") or params.get("weighting") or "binary")
    d0   = params.get("threshold", None)
    beta = params.get("beta", None)
    eps  = float(params.get("eps", 1e-6))

    # ------------------------------------------------------------------
    # binary
    # ------------------------------------------------------------------
    if decay_fn == "binary":
        if d0 is None:
            return lambda d: np.ones_like(d, dtype=float)
        return lambda d: (d <= float(d0)).astype(float)

    # ------------------------------------------------------------------
    # step
    # ------------------------------------------------------------------
    if decay_fn == "step":
        bands = params.get("distance_bands", None)
        if not bands:
            raise ValueError(
                "decay_function='step' 을 사용하려면 params['distance_bands']가 필요합니다. "
                "예: [[0, 5000, 1.0], [5000, 10000, 0.7], [10000, 20000, 0.3]]"
            )
        bands_t = [(float(lo), float(hi), float(w)) for lo, hi, w in bands]

        def w_step(d: np.ndarray) -> np.ndarray:
            out = np.zeros_like(d, dtype=float)
            for lo, hi, w in bands_t:
                mask = (d > lo) & (d <= hi)
                out[mask] = w
            return out

        return w_step

    # ------------------------------------------------------------------
    # gaussian   w(d) = exp(-d^2 / (2 * beta^2))
    # ------------------------------------------------------------------
    if decay_fn == "gaussian":
        if beta is None:
            raise ValueError(
                "decay_function='gaussian' 을 사용하려면 params['beta']가 필요합니다. "
                "beta = bandwidth sigma (단위: meters). 예: beta=5000 → 5km 반경에서 ~60% 가중치"
            )
        beta = float(beta)
        if d0 is None:
            return lambda d: np.exp(-(d ** 2) / (2.0 * beta ** 2))
        d0 = float(d0)
        return lambda d: np.where(d <= d0, np.exp(-(d ** 2) / (2.0 * beta ** 2)), 0.0)

    # ------------------------------------------------------------------
    # exponential   w(d) = exp(-beta * d)
    # ------------------------------------------------------------------
    if decay_fn == "exponential":
        if beta is None:
            raise ValueError(
                "decay_function='exponential' 을 사용하려면 params['beta']가 필요합니다. "
                "예: exp(-beta * d)"
            )
        beta = float(beta)
        if d0 is None:
            return lambda d: np.exp(-beta * d)
        d0 = float(d0)
        return lambda d: np.where(d <= d0, np.exp(-beta * d), 0.0)

    # ------------------------------------------------------------------
    # power   w(d) = (d + eps)^(-beta)
    # ------------------------------------------------------------------
    if decay_fn == "power":
        if beta is None:
            raise ValueError(
                "decay_function='power' 을 사용하려면 params['beta']가 필요합니다. "
                "예: (d + eps)^(-beta)"
            )
        beta = float(beta)
        if d0 is None:
            return lambda d: np.power(d + eps, -beta)
        d0 = float(d0)
        return lambda d: np.where(d <= d0, np.power(d + eps, -beta), 0.0)

    raise ValueError(
        f"지원하지 않는 decay_function입니다: '{decay_fn}'. "
        "사용 가능: 'binary', 'step', 'gaussian', 'exponential', 'power'"
    )


# =============================================================================
# OD builders
# =============================================================================
def build_od_euclidean_within(
    gdf_demand: gpd.GeoDataFrame,
    gdf_supply: gpd.GeoDataFrame,
    max_cost: float,
) -> pd.DataFrame:
    """Build sparse OD pairs within max_cost (meters) via spatial join."""
    if max_cost <= 0:
        raise ValueError("max_cost는 0보다 커야 합니다.")

    demand_pts = gdf_demand[[DEMAND_ID, "geometry"]].copy()
    demand_pts["geometry"] = _representative_points(gdf_demand)

    demand_buf = demand_pts.copy()
    demand_buf["geometry"] = demand_buf.geometry.buffer(float(max_cost))

    supply_pts = gdf_supply[[SUPPLY_ID, "geometry"]].copy()

    joined = gpd.sjoin(supply_pts, demand_buf, how="inner", predicate="within")
    joined = joined.merge(
        demand_pts[[DEMAND_ID, "geometry"]].rename(columns={"geometry": "demand_geom"}),
        on=DEMAND_ID,
        how="left",
    )
    joined["cost"] = joined.geometry.distance(joined["demand_geom"])
    od = joined[[DEMAND_ID, SUPPLY_ID, "cost"]].copy()
    od = od[od["cost"] <= float(max_cost)].reset_index(drop=True)
    od = od.rename(columns={"cost": OD_COST})
    return od


def build_od_euclidean_k_nearest(
    gdf_demand: gpd.GeoDataFrame,
    gdf_supply: gpd.GeoDataFrame,
    k: int,
) -> pd.DataFrame:
    """Build k-nearest OD pairs using KDTree."""
    if k is None or int(k) <= 0:
        raise ValueError("k는 1 이상의 정수여야 합니다.")
    k = int(k)

    demand_pts = gdf_demand[[DEMAND_ID, "geometry"]].copy()
    demand_pts["geometry"] = _representative_points(gdf_demand)
    supply_pts = gdf_supply[[SUPPLY_ID, "geometry"]].copy()

    demand_xy = np.vstack([demand_pts.geometry.x.values, demand_pts.geometry.y.values]).T
    supply_xy = np.vstack([supply_pts.geometry.x.values, supply_pts.geometry.y.values]).T

    if len(supply_xy) == 0:
        raise ValueError("공급(supply) 데이터가 비어있습니다.")
    if len(demand_xy) == 0:
        raise ValueError("수요(demand) 데이터가 비어있습니다.")

    tree = cKDTree(supply_xy)
    dists, idxs = tree.query(demand_xy, k=min(k, len(supply_xy)))

    if dists.ndim == 1:
        dists = dists[:, None]
        idxs  = idxs[:, None]

    demand_ids = demand_pts[DEMAND_ID].to_numpy()
    supply_ids = supply_pts[SUPPLY_ID].to_numpy()

    od_rows = []
    for row_i, did in enumerate(demand_ids):
        for nn_j in range(dists.shape[1]):
            od_rows.append((did, supply_ids[idxs[row_i, nn_j]], float(dists[row_i, nn_j])))

    return pd.DataFrame(od_rows, columns=[OD_DEMAND, OD_SUPPLY, OD_COST])


def load_od_precomputed_csv(od_csv_path: str, od_cols: Tuple[str, str, str]) -> pd.DataFrame:
    """Load OD pairs from a precomputed CSV."""
    if not od_csv_path or not os.path.exists(od_csv_path):
        raise FileNotFoundError(f"OD CSV 파일이 없습니다: {od_csv_path}")

    demand_col, supply_col, cost_col = od_cols
    od = pd.read_csv(od_csv_path)
    missing = [c for c in [demand_col, supply_col, cost_col] if c not in od.columns]
    if missing:
        raise ValueError(f"OD CSV에 필요한 컬럼이 없습니다: {missing}. 현재 컬럼: {list(od.columns)}")

    od = od[[demand_col, supply_col, cost_col]].copy()
    od = od.rename(columns={demand_col: OD_DEMAND, supply_col: OD_SUPPLY, cost_col: OD_COST})
    return od


# =============================================================================
# 1) load_data
# =============================================================================
def load_data(
    target_subject: str,
    region_filter: Optional[str],
    data_config: DataConfig,
    *,
    column_map: Optional[dict] = None,
    od_config: Optional[ODConfig] = None,
) -> Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, Optional[pd.DataFrame], gpd.GeoDataFrame, Optional[gpd.GeoDataFrame], Dict[str, Any]]:
    """
    Load and standardize demand/supply data, and (optionally) build OD data.

    Returns:
      gdf_demand_std   : demand GDF  (demand_id, demand_name, demand_pop, geometry)
      gdf_supply_std   : supply GDF  (supply_id, supply_cap, geometry)
      od_df            : OD DataFrame (demand_id, supply_id, cost) or None
      shp_unit         : unit boundary GDF (demand_id, demand_name, geometry)
      shp_boundary     : boundary overlay GDF or None
      context          : metadata dict
    """
    cfg = data_config
    ctx: Dict[str, Any] = {}

    pop_path      = os.path.join(cfg.data_dir, cfg.file_pop)
    supply_path   = os.path.join(cfg.data_dir, cfg.file_supply)
    shp_unit_path = os.path.join(cfg.data_dir, cfg.file_shp_unit)
    shp_boundary_path = os.path.join(cfg.data_dir, cfg.file_shp_boundary) if cfg.file_shp_boundary else None

    for p in [pop_path, supply_path, shp_unit_path]:
        if not os.path.exists(p):
            raise FileNotFoundError(f"필수 파일이 없습니다: {p}")


    ctx["pop_path"] = pop_path
    ctx["supply_path"] = supply_path
    ctx["shp_unit_path"] = shp_unit_path
    ctx["shp_boundary_path"] = shp_boundary_path
    # --- Supply ---
    df_supply_raw = pd.read_csv(supply_path)
    if target_subject not in df_supply_raw.columns:
        raise ValueError(
            f"공급 데이터에 '{target_subject}' 컬럼이 없습니다. "
            f"사용 가능한 컬럼 예시: {list(df_supply_raw.columns)[:30]}"
        )

    supply_x_col    = (column_map or {}).get("supply_x_col")    or _first_existing(df_supply_raw.columns, ["좌표(X)", "X", "lon", "longitude", "LON"])
    supply_y_col    = (column_map or {}).get("supply_y_col")    or _first_existing(df_supply_raw.columns, ["좌표(Y)", "Y", "lat", "latitude", "LAT"])
    supply_id_col   = (column_map or {}).get("supply_id_col")   or _first_existing(df_supply_raw.columns, ["요양기관번호", "기관ID", "ID", "id"])
    supply_name_col = (column_map or {}).get("supply_name_col") or _first_existing(df_supply_raw.columns, ["요양기관명", "기관명", "name"])

    if supply_x_col is None or supply_y_col is None:
        raise ValueError("공급 데이터에서 좌표 컬럼을 찾지 못했습니다. column_map으로 supply_x_col/supply_y_col을 지정해주세요.")

    df_supply = df_supply_raw.copy()
    df_supply = df_supply.dropna(subset=[supply_x_col, supply_y_col])
    df_supply = df_supply[df_supply[target_subject] > 0].copy()

    df_supply[SUPPLY_ID]   = df_supply[supply_id_col].astype(str)   if supply_id_col   and supply_id_col   in df_supply.columns else df_supply.index.astype(str)
    df_supply[SUPPLY_NAME] = df_supply[supply_name_col].astype(str) if supply_name_col and supply_name_col in df_supply.columns else ""
    df_supply[SUPPLY_CAP]  = pd.to_numeric(df_supply[target_subject], errors="coerce").fillna(0)

    geometry   = [Point(xy) for xy in zip(df_supply[supply_x_col], df_supply[supply_y_col])]
    gdf_supply = gpd.GeoDataFrame(df_supply, geometry=geometry, crs=cfg.crs_supply_xy)
    gdf_supply = _ensure_crs(gdf_supply, cfg.crs_analysis)

    ctx.update({
        "supply_x_col": supply_x_col, "supply_y_col": supply_y_col,
        "supply_id_col": supply_id_col, "supply_name_col": supply_name_col,
        "supply_capacity_col_source": target_subject,
    })
    gdf_supply_std = gdf_supply[[SUPPLY_ID, SUPPLY_NAME, SUPPLY_CAP, "geometry"]].copy()

    # --- Demand ---
    df_pop_raw  = pd.read_csv(pop_path)
    gdf_shp     = _ensure_crs(gpd.read_file(shp_unit_path), cfg.crs_analysis)

    pop_code_col     = (column_map or {}).get("pop_code_col")     or _first_existing(df_pop_raw.columns, ["ADM_DR_CD_8", "ADM_DR_CD_7", "ADM_CD", "CODE", "code"])
    shp_code_col     = (column_map or {}).get("shp_code_col")     or _first_existing(gdf_shp.columns,    ["ADM_CD", "ADM_DR_CD", "CODE", "code"])
    pop_col          = (column_map or {}).get("pop_col")          or _first_existing(df_pop_raw.columns, ["총 인구수", "인구", "population", "POP"])
    demand_name_col  = (column_map or {}).get("demand_name_col")  or _first_existing(df_pop_raw.columns, ["행정기관", "ADM_NM", "name", "NAME"])

    if pop_code_col is None or shp_code_col is None:
        raise ValueError("수요(pop) CSV와 SHP를 join할 코드 컬럼을 찾지 못했습니다. column_map으로 pop_code_col/shp_code_col 지정 필요.")

    if pop_col is None:
        num_cols = df_pop_raw.select_dtypes(include="number").columns.tolist()
        if not num_cols:
            raise ValueError("인구(population) 컬럼을 찾지 못했고, 숫자형 컬럼도 없습니다.")
        pop_col = num_cols[0]

    df_pop_raw[pop_code_col] = df_pop_raw[pop_code_col].astype(str)
    gdf_shp[shp_code_col]    = gdf_shp[shp_code_col].astype(str)

    gdf_demand = gdf_shp.merge(df_pop_raw, left_on=shp_code_col, right_on=pop_code_col, how="inner")
    gdf_demand[DEMAND_NAME] = gdf_demand[demand_name_col].astype(str) if demand_name_col and demand_name_col in gdf_demand.columns else gdf_demand[shp_code_col].astype(str)
    gdf_demand[DEMAND_ID]   = gdf_demand[shp_code_col].astype(str)
    gdf_demand[DEMAND_POP]  = pd.to_numeric(gdf_demand[pop_col], errors="coerce").fillna(0)

    ctx.update({
        "pop_code_col": pop_code_col, "shp_code_col": shp_code_col,
        "pop_col_source": pop_col, "demand_name_col_source": demand_name_col,
    })

    if region_filter:
        gdf_demand, region_terms = _filter_by_region_terms(gdf_demand, DEMAND_NAME, region_filter)
        if region_terms:
            ctx["region_filter_terms"] = region_terms
        if gdf_demand.empty:
            raise ValueError(
                f"region_filter='{region_filter}'(확장={region_terms})에 해당하는 수요 데이터가 없습니다."
            )

    gdf_demand_std = gdf_demand[[DEMAND_ID, DEMAND_NAME, DEMAND_POP, "geometry"]].copy()

    # --- Shapefiles (outputs) ---
    shp_unit = gdf_demand[[DEMAND_ID, DEMAND_NAME, "geometry"]].copy()
    shp_boundary: Optional[gpd.GeoDataFrame] = None
    if shp_boundary_path and os.path.exists(shp_boundary_path):
        try:
            shp_boundary = _ensure_crs(gpd.read_file(shp_boundary_path), cfg.crs_analysis)
            bbox = _expand_bounds(gdf_demand_std.total_bounds, pad_ratio=0.10)
            shp_boundary = shp_boundary.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]].copy()
        except Exception:
            shp_boundary = None

    # --- Supply bbox reduction ---
    if not gdf_demand_std.empty and not gdf_supply_std.empty:
        if od_config is not None and od_config.max_cost is not None and str(od_config.mode).lower() == "euclidean_within":
            demand_pts_for_filter = gdf_demand_std.copy()
            demand_pts_for_filter["geometry"] = _representative_points(gdf_demand_std)
            study_area = demand_pts_for_filter.unary_union.buffer(float(od_config.max_cost))
            gdf_supply_std = gdf_supply_std[gdf_supply_std.geometry.intersects(study_area)].copy()
        else:
            bounds = _expand_bounds(gdf_demand_std.total_bounds, pad_ratio=0.10)
            gdf_supply_std = gdf_supply_std.cx[bounds[0]:bounds[2], bounds[1]:bounds[3]].copy()

    # --- OD build/load ---
    od_df: Optional[pd.DataFrame] = None
    if od_config is not None:
        mode = str(od_config.mode).strip().lower()
        if mode == "euclidean_within":
            if od_config.max_cost is None:
                raise ValueError("ODConfig.mode='euclidean_within'이면 max_cost가 필요합니다.")
            od_df = build_od_euclidean_within(gdf_demand_std, gdf_supply_std, float(od_config.max_cost))
        elif mode == "euclidean_k_nearest":
            if od_config.k is None:
                raise ValueError("ODConfig.mode='euclidean_k_nearest'이면 k가 필요합니다.")
            od_df = build_od_euclidean_k_nearest(gdf_demand_std, gdf_supply_std, int(od_config.k))
        elif mode == "precomputed_csv":
            if not od_config.od_csv_path:
                raise ValueError("ODConfig.mode='precomputed_csv'이면 od_csv_path가 필요합니다.")
            od_df = load_od_precomputed_csv(od_config.od_csv_path, od_config.od_cols)
        else:
            raise ValueError(f"지원하지 않는 ODConfig.mode입니다: {od_config.mode}")

    ctx["crs_analysis"] = cfg.crs_analysis
    return gdf_demand_std, gdf_supply_std, od_df, shp_unit, shp_boundary, ctx


# =============================================================================
# 2) calculate_accessibility
# =============================================================================
def calculate_accessibility(
    gdf_demand: gpd.GeoDataFrame,
    gdf_supply: gpd.GeoDataFrame,
    mat_od: pd.DataFrame,
    method: str,
    params: Optional[dict] = None,
) -> Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, Dict[str, Any]]:
    """
    Compute accessibility score for each demand unit.

    params keys used per method:
      All methods  : result_col (str, default "accessibility")
      MIN / K_AVG  : k (int, for K_AVG)
      COM          : threshold (float, meters)
      GRAVITY      : threshold (float, meters, optional dmax cutoff),
                     distance_decay_function (str), beta (float), distance_bands (list)
      2SFCA        : threshold (float, meters)
      E2SFCA       : threshold (float, meters),
                     distance_decay_function (str), beta (float), distance_bands (list)
      PPR          : threshold (float, meters), ratio_type (str), scale (float)
    """
    if params is None:
        params = {}

    m          = _normalize_method(method)
    result_col = params.get("result_col", "accessibility")

    demand = gdf_demand.copy()
    supply = gdf_supply.copy()

    if mat_od is None or mat_od.empty:
        raise ValueError("mat_od(OD 데이터)가 비어있습니다.")

    for c in [OD_DEMAND, OD_SUPPLY, OD_COST]:
        if c not in mat_od.columns:
            raise ValueError(f"OD 데이터에 '{c}' 컬럼이 없습니다. 현재 컬럼: {list(mat_od.columns)}")

    od = mat_od[[OD_DEMAND, OD_SUPPLY, OD_COST]].copy()
    od = od.merge(demand[[DEMAND_ID, DEMAND_POP]], left_on=OD_DEMAND, right_on=DEMAND_ID, how="left")
    od = od.merge(supply[[SUPPLY_ID, SUPPLY_CAP]], left_on=OD_SUPPLY, right_on=SUPPLY_ID, how="left")
    od = od.dropna(subset=[DEMAND_POP, SUPPLY_CAP]).copy()

    info: Dict[str, Any] = {"method": m, "result_col": result_col}

    # ---- MIN ----
    if m == "MIN":
        s = od.groupby(OD_DEMAND)[OD_COST].min()
        demand[result_col] = demand[DEMAND_ID].map(s).fillna(np.nan)
        info["direction"] = "lower_is_better"
        return demand, supply, info

    # ---- K_AVG ----
    if m == "K_AVG":
        k = params.get("k", None)
        if k is None:
            raise ValueError("K_AVG method에는 params['k']가 필요합니다.")
        k = int(k)
        if k <= 0:
            raise ValueError("k는 1 이상의 정수여야 합니다.")
        def _kavg(x: pd.Series) -> float:
            return float(x.nsmallest(k).mean()) if len(x) else np.nan
        s = od.groupby(OD_DEMAND)[OD_COST].apply(_kavg)
        demand[result_col] = demand[DEMAND_ID].map(s).fillna(np.nan)
        info["direction"] = "lower_is_better"
        return demand, supply, info

    # ---- COM ----
    if m == "COM":
        d0 = params.get("threshold", None)
        if d0 is None:
            raise ValueError("COM method에는 params['threshold']가 필요합니다 (meters).")
        d0  = float(d0)
        od2 = od[od[OD_COST] <= d0].copy()
        od2["_S"] = od2[SUPPLY_CAP].astype(float) if params.get("use_capacity", True) else 1.0
        s = od2.groupby(OD_DEMAND)["_S"].sum()
        demand[result_col] = demand[DEMAND_ID].map(s).fillna(0)
        info["direction"] = "higher_is_better"
        return demand, supply, info

    # ---- GRAVITY ----
    if m == "GRAVITY":
        df_in = params.get("distance_decay_function") or params.get("decay_function") or "exponential"
        decay_params = {
            "distance_decay_function": df_in,
            "threshold":              params.get("dmax", None),
            "beta":                   params.get("beta", None),
            "distance_bands":         params.get("distance_bands", None),
            "eps":                    params.get("eps", 1e-6),
        }
        f   = build_decay_function(decay_params)
        od2 = od[od[OD_COST] <= float(decay_params["threshold"])].copy() if decay_params["threshold"] is not None else od.copy()
        od2["_term"] = od2[SUPPLY_CAP].astype(float).to_numpy() * f(od2[OD_COST].to_numpy()) if params.get("use_capacity", True) else f(od2[OD_COST].to_numpy())
        s = od2.groupby(OD_DEMAND)["_term"].sum()
        demand[result_col] = demand[DEMAND_ID].map(s).fillna(0)
        info["direction"] = "higher_is_better"
        info["decay"]     = decay_params
        return demand, supply, info

    # ---- 2SFCA / E2SFCA ----
    if m in {"2SFCA", "E2SFCA"}:
        d0 = params.get("threshold", None)
        if d0 is None:
            raise ValueError(f"{m} method에는 params['threshold']가 필요합니다 (meters).")
        d0  = float(d0)
        od2 = od[od[OD_COST] <= d0].copy()

        if m == "2SFCA":
            od2["_w"] = 1.0
        else:
            # E2SFCA: use decay_function for within-catchment weights
            df_in = params.get("distance_decay_function") or params.get("decay_function") or None
            w_params = {
                "distance_decay_function": df_in,
                "threshold":               d0,
                "distance_bands":          params.get("distance_bands", None),
                "beta":                    params.get("beta", None),
                "eps":                     params.get("eps", 1e-6),
            }
            if not w_params["distance_decay_function"]:
                raise ValueError(
                    "E2SFCA는 params['distance_decay_function']이 필요합니다. "
                    "사용 가능: 'step', 'gaussian', 'exponential', 'power'"
                )
            w = build_decay_function(w_params)
            od2["_w"] = w(od2[OD_COST].to_numpy())

        # Step 1: R_j = S_j / sum_k(P_k * w_kj)
        denom = od2.groupby(OD_SUPPLY).apply(lambda df: float((df[DEMAND_POP] * df["_w"]).sum()))
        denom.name = "_denom"
        supply["_denom"] = supply[SUPPLY_ID].map(denom).fillna(0)
        supply["R_j"]    = _safe_div(supply[SUPPLY_CAP].astype(float), supply["_denom"].astype(float))

        # Step 2: A_i = sum_j(R_j * w_ij)
        od3          = od2.merge(supply[[SUPPLY_ID, "R_j"]], left_on=OD_SUPPLY, right_on=SUPPLY_ID, how="left")
        od3["_term"] = od3["R_j"].astype(float) * od3["_w"].astype(float)
        score        = od3.groupby(OD_DEMAND)["_term"].sum()
        demand[result_col] = demand[DEMAND_ID].map(score).fillna(0)

        info["direction"] = "higher_is_better"
        info["threshold"] = d0
        return demand, supply, info

    # ---- PPR ----
    if m == "PPR":
        d0 = params.get("threshold", None)
        if d0 is None:
            raise ValueError("PPR method에는 params['threshold']가 필요합니다.")
        d0         = float(d0)
        ratio_type = str(params.get("ratio_type", "population_per_supply")).strip().lower()
        scale      = float(params.get("scale", 1.0))
        od2        = od[od[OD_COST] <= d0].copy()
        supply_sum = od2.groupby(OD_DEMAND)[SUPPLY_CAP].sum()
        demand["_supply_sum"] = demand[DEMAND_ID].map(supply_sum).fillna(0)

        if ratio_type in {"population_per_supply", "ppr"}:
            demand[result_col] = (demand[DEMAND_POP].astype(float) * scale) / demand["_supply_sum"].replace({0: np.nan})
            info["direction"]  = "lower_is_better"
        elif ratio_type in {"supply_per_population", "spr"}:
            demand[result_col] = (demand["_supply_sum"].astype(float) * scale) / demand[DEMAND_POP].replace({0: np.nan})
            demand[result_col] = demand[result_col].fillna(0)
            info["direction"]  = "higher_is_better"
        else:
            raise ValueError("ratio_type은 'population_per_supply' 또는 'supply_per_population'만 지원합니다.")

        info["threshold"]  = d0
        info["ratio_type"] = ratio_type
        return demand, supply, info

    raise ValueError(f"지원하지 않는 분석 방법입니다: {method} (정규화 후: {m})")


# =============================================================================
# 3) visualize_accessibility
# =============================================================================
def visualize_accessibility(
    gdf_demand_out: gpd.GeoDataFrame,
    gdf_supply_out: gpd.GeoDataFrame,
    mat_od: Optional[pd.DataFrame],
    method: str,
    params: Optional[dict] = None,
    *,
    sigungu_gdf: Optional[gpd.GeoDataFrame] = None,
    output_dir: str = ".",
) -> Tuple[str, str]:
    """Visualize accessibility results; return (summary_markdown, image_path)."""
    if params is None:
        params = {}
    m          = _normalize_method(method)
    result_col = params.get("result_col", "accessibility")

    if result_col not in gdf_demand_out.columns:
        raise ValueError(f"gdf_demand_out에 '{result_col}' 컬럼이 없습니다.")

    direction = "higher_is_better"
    if m in {"MIN", "K_AVG"}:
        direction = "lower_is_better"
    if m == "PPR" and str(params.get("ratio_type", "population_per_supply")).strip().lower() in {"population_per_supply", "ppr"}:
        direction = "lower_is_better"

    df        = gdf_demand_out[[DEMAND_NAME, result_col]].dropna(subset=[result_col]).copy()
    ascending = direction == "lower_is_better"
    df_sorted = df.sort_values(result_col, ascending=ascending).head(int(params.get("top_n", 20)))

    stats = {k: (float(getattr(df[result_col], k)()) if len(df) else float("nan"))
             for k in ["min", "max", "mean", "median"]}
    stats["count"] = int(df[result_col].count())

    summary_md = (
        f"**[요약 통계]**\n"
        f"- metric: {m}\n"
        f"- direction: {direction}\n"
        f"- count: {stats['count']}\n"
        f"- min / median / mean / max: "
        f"{stats['min']:.4g} / {stats['median']:.4g} / {stats['mean']:.4g} / {stats['max']:.4g}\n"
        f"\n**[상위(또는 하위) {len(df_sorted)}개 지역]**\n{df_sorted.to_markdown(index=False)}\n"
    )

    os.makedirs(output_dir, exist_ok=True)
    title      = params.get("title", f"Accessibility ({m})")
    output_img = os.path.join(output_dir, params.get("output_img", f"result_{m}.png"))

    fig, ax = plt.subplots(1, 1, figsize=(12, 10))
    cmap = params.get("cmap", "YlOrRd")
    if direction == "lower_is_better" and str(params.get("reverse_cmap_for_impedance", "true")).lower() in {"true", "1", "yes"}:
        cmap = cmap + "_r" if not cmap.endswith("_r") else cmap

    gdf_demand_out.plot(
        column=result_col, ax=ax, legend=True, cmap=cmap,
        edgecolor="gray", linewidth=0.1,
        legend_kwds={"label": f"{m} ({result_col})", "orientation": "horizontal"},
    )
    if sigungu_gdf is not None and not sigungu_gdf.empty:
        try:
            sigungu_gdf.plot(ax=ax, facecolor="none", edgecolor="darkgrey", linewidth=1.5)
        except Exception:
            pass
    if gdf_supply_out is not None and not gdf_supply_out.empty:
        size_mode    = str(params.get("supply_marker_size", "capacity")).lower()
        marker_sizes = (
            20 + gdf_supply_out[SUPPLY_CAP].astype(float).clip(lower=0) * float(params.get("supply_size_scale", 10))
            if size_mode == "capacity" and SUPPLY_CAP in gdf_supply_out.columns
            else float(params.get("supply_marker_const", 25))
        )
        gdf_supply_out.plot(
            ax=ax, color=params.get("supply_color", "royalblue"),
            markersize=marker_sizes, alpha=float(params.get("supply_alpha", 0.6)),
            edgecolor="white",
        )

    ax.set_title(title, fontsize=14)
    ax.set_axis_off()
    bounds = _expand_bounds(gdf_demand_out.total_bounds, pad_ratio=0.10)
    ax.set_xlim(bounds[0], bounds[2])
    ax.set_ylim(bounds[1], bounds[3])
    plt.savefig(output_img, dpi=300, bbox_inches="tight") # 고화질 원본

    # embed용 저장
    embed_img = output_img.replace(".png", "_embed.png")
    # plt.savefig(embed_img, dpi=100, bbox_inches="tight") # 저장은 잠시 제외
    plt.close(fig)

    return summary_md, output_img, embed_img



# =============================================================================
# Wizard catalog (for metric / parameter guidance)
# =============================================================================
# NOTE:
# - This catalog mirrors the logic used in `accessibility_wizard_v3.jsx`.
# - It is intentionally "data-only" so both chat-based flows and UI-based flows
#   can reuse the same recommended defaults.
#
# Unit conventions used by THIS server:
# - threshold_km is in kilometers (converted to meters internally).
# - OD distance/cost is computed in meters (EPSG:5179).
# - For exponential decay, this implementation uses: exp(-beta * d_meters).
#   Therefore:
#     beta(1/km)  -> beta_per_meter = beta / 1000
#   The wizard defaults in the JSX assume beta is in 1/km, so we convert by default.
# =============================================================================

WIZARD_SERVICE_TYPES: Dict[str, Dict[str, Any]] = {
    "time-critical": {
        "label": "Type I: Time-critical",
        "sublabel": "응급 / 골든타임",
        "determinant": "Impedance-dominant",
        "examples": ["응급실", "분만 응급", "소방서", "재난 대피소", "외상센터"],
        "description": "빠른 도달이 핵심인 서비스. 가장 가까운 시설까지의 시간/거리가 곧 성과 지표.",
        "warning": "2SFCA 계열은 불필요한 파라미터를 추가하며 이 맥락에서 권장하지 않습니다.",
        "metrics": [
            {
                "id": "MIN",
                "name": "Nearest Impedance (MIN)",
                "badge": "추천",
                "tool_method": "MIN",
                "has_decay": False,
                "static_params": [],
            },
            {
                "id": "K_AVG",
                "name": "k-Nearest Average (k-AVG)",
                "badge": "선택적",
                "tool_method": "K_AVG",
                "has_decay": False,
                "static_params": [
                    {"key": "k", "label": "k (고려할 시설 수)", "type": "number", "default": 3, "min": 2, "max": 10},
                ],
            },
        ],
    },
    "opportunity": {
        "label": "Type II: Opportunity-driven",
        "sublabel": "일상 이용 / 선택지 다양성",
        "determinant": "Opportunity-dominant",
        "examples": ["공원", "식료품점", "문화시설", "대형마트", "약국"],
        "description": "도달 가능한 기회의 양이 핵심. 얼마나 많은 선택지가 이동 가능 범위 내 있는가.",
        "warning": None,
        "metrics": [
            {
                "id": "COM",
                "name": "Cumulative Opportunities (COM)",
                "badge": "추천",
                "tool_method": "COM",
                "has_decay": False,
                "static_params": [
                    {"key": "threshold_km", "label": "Catchment 반경 d₀ (km)", "type": "number", "default": 10, "min": 1, "max": 50},
                ],
            },
            {
                "id": "GRAVITY",
                "name": "Gravity-based Measure",
                "badge": "고급",
                "tool_method": "GRAVITY",
                "has_decay": True,
                "decay_options": ["exponential", "gaussian", "power", "binary"],
                "default_decay": "exponential",
                "static_params": [
                    {"key": "threshold_km", "label": "최대 반경 (km)", "type": "number", "default": 15, "min": 1, "max": 50},
                ],
            },
        ],
    },
    "capacity": {
        "label": "Type III: Capacity-constrained",
        "sublabel": "공급-수요 경쟁 / 형평성 진단",
        "determinant": "Competition-dominant",
        "examples": ["1차 의료", "산부인과 (정기검진)", "소아과", "학교", "어린이집"],
        "description": "공급 용량이 수요와 경쟁하는 서비스. 시설이 근처에 있더라도 수요 과잉이면 실질 접근성은 낮다.",
        "warning": "proximity 지표만 사용하면 수요 경쟁으로 인한 실질적 접근성 부족을 과소평가합니다.",
        "metrics": [
            {
                "id": "2SFCA",
                "name": "Two-Step FCA (2SFCA)",
                "badge": "추천",
                "tool_method": "2SFCA",
                "has_decay": False,
                "static_params": [
                    {"key": "threshold_km", "label": "Catchment 반경 d₀ (km)", "type": "number", "default": 10, "min": 1, "max": 50},
                ],
            },
            {
                "id": "E2SFCA",
                "name": "Enhanced 2SFCA (E2SFCA)",
                "badge": "고급",
                "tool_method": "E2SFCA",
                "has_decay": True,
                "decay_options": ["gaussian", "exponential", "step", "power", "binary"],
                "default_decay": "gaussian",
                "static_params": [
                    {"key": "threshold_km", "label": "Catchment 반경 d₀ (km)", "type": "number", "default": 10, "min": 1, "max": 50},
                ],
            },
        ],
    },
}

WIZARD_DECAY_META: Dict[str, Dict[str, Any]] = {
    "binary": {
        "label": "Binary (hard cutoff)",
        "params": [],
        "beta_unit": None,
        "distance_bands_required": False,
    },
    "step": {
        "label": "Step (구간별 가중치)",
        "params": [
            {"key": "distance_bands_json", "type": "textarea",
             "default": "[[0, 5000, 1.0], [5000, 10000, 0.7], [10000, 20000, 0.3]]",
             "unit": "[[시작(m), 끝(m), 가중치], ...]"}
        ],
        "beta_unit": None,
        "distance_bands_required": True,
    },
    "gaussian": {
        "label": "Gaussian",
        "params": [
            {"key": "beta", "type": "number", "default": 5000, "min": 500, "max": 30000, "step": 500, "unit": "m"},
        ],
        "beta_unit": "m",
        "distance_bands_required": False,
    },
    "exponential": {
        "label": "Exponential",
        "params": [
            {"key": "beta", "type": "number", "default": 0.1, "min": 0.01, "max": 2.0, "step": 0.01, "unit": "1/km"},
        ],
        # Wizard default assumes 1/km; we convert to 1/m for internal computation.
        "beta_unit": "1/km",
        "distance_bands_required": False,
    },
    "power": {
        "label": "Power",
        "params": [
            {"key": "beta", "type": "number", "default": 2.0, "min": 0.5, "max": 4.0, "step": 0.5, "unit": "dimensionless"},
        ],
        "beta_unit": "dimensionless",
        "distance_bands_required": False,
    },
}

def _wizard_find_metric(service_type: str, metric_id: str) -> Dict[str, Any]:
    st = WIZARD_SERVICE_TYPES.get(service_type)
    if not st:
        raise ValueError(f"지원하지 않는 service_type입니다: {service_type}. 가능: {list(WIZARD_SERVICE_TYPES.keys())}")
    for m in st.get("metrics", []):
        if str(m.get("id")) == str(metric_id):
            return m
    raise ValueError(f"service_type='{service_type}'에서 metric_id='{metric_id}'를 찾지 못했습니다.")

def _wizard_default_static_params(metric: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for p in metric.get("static_params", []) or []:
        if "key" in p and "default" in p:
            out[p["key"]] = p["default"]
    return out

def _wizard_default_decay_params(decay_fn: str) -> Dict[str, Any]:
    meta = WIZARD_DECAY_META.get(decay_fn, {})
    out: Dict[str, Any] = {}
    for p in meta.get("params", []) or []:
        if "key" in p and "default" in p:
            out[p["key"]] = p["default"]
    return out

def _wizard_convert_beta_if_needed(decay_fn: str, beta: Optional[float], *, beta_unit: Optional[str] = None) -> Optional[float]:
    if beta is None:
        return None
    df = _normalize_decay_function(decay_fn)
    if df != "exponential":
        return float(beta)
    # default: wizard assumes 1/km
    unit = (beta_unit or WIZARD_DECAY_META.get("exponential", {}).get("beta_unit") or "1/km").strip().lower()
    if unit in {"1/km", "per_km", "km^-1"}:
        return float(beta) / 1000.0
    # already per-meter
    return float(beta)

@mcp.tool()
def wizard_list_service_types() -> str:
    """List available service types for the accessibility wizard."""
    payload = {
        "service_types": [
            {
                "id": k,
                "label": v.get("label"),
                "sublabel": v.get("sublabel"),
                "determinant": v.get("determinant"),
                "examples": v.get("examples"),
                "description": v.get("description"),
                "warning": v.get("warning"),
                "recommended_metrics": [m["id"] for m in v.get("metrics", []) if m.get("badge") == "추천"],
            }
            for k, v in WIZARD_SERVICE_TYPES.items()
        ]
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)

@mcp.tool()
def wizard_list_metrics(service_type: str) -> str:
    """List metrics for a given service type."""
    st = WIZARD_SERVICE_TYPES.get(service_type)
    if not st:
        return f"지원하지 않는 service_type입니다: {service_type}. 가능: {list(WIZARD_SERVICE_TYPES.keys())}"
    payload = {
        "service_type": service_type,
        "metrics": st.get("metrics", []),
        "warning": st.get("warning"),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)

@mcp.tool()
def wizard_build_job(
    region_filter: str,
    target_subject: str,
    service_type: str,
    metric_id: str,
    *,
    # optional overrides
    threshold_km: Optional[float] = None,
    k: Optional[int] = None,
    distance_decay_function: Optional[str] = None,
    beta: Optional[float] = None,
    beta_unit: Optional[str] = None,
    distance_bands_json: Optional[str] = None,
    ratio_type: Optional[str] = None,
    # wizard control
    finalize: bool = False,
    accept_defaults: bool = False,
) -> str:
    """Wizard helper to *propose* and then *finalize* an analysis job.

    *** AGENT INSTRUCTIONS — READ BEFORE CALLING ***
    1. Always call with finalize=False first (default).
       Show the returned PARAM_CONFIRM_REQUIRED payload to the user verbatim.
    2. STOP after showing the checklist. Do NOT immediately call finalize=True.
    3. Wait for the user to explicitly approve or modify each parameter in chat.
    4. Only call with finalize=True (or accept_defaults=True) AFTER the user
       has sent an explicit approval message.
    5. Never infer user approval from context. Require an explicit message.

    Why this exists
    ---------------
    LLMs tend to "helpfully" pick parameters and execute immediately.
    To provide a true chatbot-like wizard experience, this tool works in two phases:

    1) Preview (default: finalize=False)
       - Returns recommended defaults + a checklist of parameters to confirm.
       - AGENT must show this to the user and wait for explicit approval.

    2) Finalize (finalize=True)
       - Only allowed when either:
         (a) user explicitly provided all required parameters, or
         (b) accept_defaults=True (user said "추천값 그대로" / "기본값으로 진행").
       - Returns a runnable payload with `run_args`.

    Notes on units
    --------------
    - threshold_km: km (converted to meters internally by `run()`)
    - Exponential decay: the UI/wizard convention is beta in 1/km, but THIS server uses exp(-beta * d_meters).
      Therefore, we convert 1/km -> 1/m by dividing by 1000 at finalize time.
    """
    metric = _wizard_find_metric(service_type, metric_id)

    # --- Proposed static params (user-level) ---
    static_params = _wizard_default_static_params(metric)
    defaults_used: List[str] = []

    # Track explicit user inputs
    provided: Dict[str, bool] = {
        "threshold_km": threshold_km is not None,
        "k": k is not None,
        "distance_decay_function": distance_decay_function is not None,
        "beta": beta is not None,
        "distance_bands_json": distance_bands_json is not None,
        "ratio_type": ratio_type is not None,
        "beta_unit": beta_unit is not None,
    }

    if threshold_km is not None:
        static_params["threshold_km"] = float(threshold_km)
    elif "threshold_km" in static_params:
        defaults_used.append("threshold_km")

    if k is not None:
        static_params["k"] = int(k)
    elif "k" in static_params:
        defaults_used.append("k")

    # --- Proposed decay (user-level) ---
    decay_fn: Optional[str] = None
    decay_params_user: Dict[str, Any] = {}
    decay_params_internal: Dict[str, Any] = {}

    if metric.get("has_decay"):
        if distance_decay_function is not None:
            decay_fn = _normalize_decay_function(distance_decay_function)
        else:
            decay_fn = _normalize_decay_function(metric.get("default_decay") or "binary")
            defaults_used.append("distance_decay_function")

        # fill defaults for the chosen decay function (user-level)
        meta = WIZARD_DECAY_META.get(decay_fn, {})
        for p in meta.get("params", []):
            key = p.get("key")
            if not key:
                continue
            if key == "beta":
                if beta is not None:
                    decay_params_user["beta"] = float(beta)
                else:
                    decay_params_user["beta"] = float(p.get("default"))
                    defaults_used.append("beta")
            elif key == "distance_bands_json":
                if distance_bands_json is not None:
                    decay_params_user["distance_bands_json"] = str(distance_bands_json)
                else:
                    decay_params_user["distance_bands_json"] = str(p.get("default"))
                    defaults_used.append("distance_bands_json")

        # Determine beta_unit for user-level display (mainly for exponential)
        inferred_beta_unit = beta_unit or meta.get("beta_unit")
        if decay_fn == "exponential" and inferred_beta_unit is None:
            inferred_beta_unit = "1/km"
        if inferred_beta_unit:
            decay_params_user["beta_unit"] = inferred_beta_unit

        # Build internal decay params for run() (convert if needed) at finalize time
        if decay_fn in {"gaussian", "power"}:
            if "beta" in decay_params_user:
                decay_params_internal["beta"] = float(decay_params_user["beta"])
        elif decay_fn == "exponential":
            if "beta" in decay_params_user:
                b = float(decay_params_user["beta"])
                unit = str(inferred_beta_unit or "1/km").strip()
                # Most users (and the JSX wizard) specify exponential beta in 1/km.
                # This server uses meters in OD cost, so convert to 1/m.
                if unit in {"1/km", "per_km", "km^-1"}:
                    decay_params_internal["beta"] = b / 1000.0
                else:
                    # assume already 1/m
                    decay_params_internal["beta"] = b
        elif decay_fn == "step":
            if "distance_bands_json" in decay_params_user:
                decay_params_internal["distance_bands_json"] = decay_params_user["distance_bands_json"]
        elif decay_fn == "binary":
            pass

    # --- Build confirmation questions ---
    questions: List[Dict[str, Any]] = []

    # Static parameters to confirm
    for p in metric.get("static_params", []):
        key = p.get("key")
        if not key:
            continue
        if not provided.get(key, False) and not accept_defaults:
            questions.append({
                "key": key,
                "label": p.get("label", key),
                "type": p.get("type", "number"),
                "default": p.get("default"),
                "current": static_params.get(key),
                "min": p.get("min"),
                "max": p.get("max"),
                "note": "추천 기본값입니다. 그대로 진행하거나 값을 수정해 주세요.",
            })

    # Decay function + its parameters to confirm
    if metric.get("has_decay"):
        if (not provided.get("distance_decay_function", False)) and not accept_defaults:
            questions.append({
                "key": "distance_decay_function",
                "label": "distance_decay_function (감쇠 함수)",
                "type": "select",
                "options": metric.get("decay_options", ["binary", "step", "gaussian", "exponential", "power"]),
                "default": metric.get("default_decay"),
                "current": decay_fn,
                "note": "추천 감쇠함수입니다. 필요하면 변경해 주세요.",
            })

        if decay_fn == "step":
            if (not provided.get("distance_bands_json", False)) and not accept_defaults:
                questions.append({
                    "key": "distance_bands_json",
                    "label": "distance_bands_json (Step 구간 가중치)",
                    "type": "textarea",
                    "default": WIZARD_DECAY_META["step"]["params"][0]["default"],
                    "current": decay_params_user.get("distance_bands_json"),
                    "unit": "[[시작(m), 끝(m), 가중치], ...]",
                    "note": "단위는 미터(m)입니다. 5km=5000.",
                })
        if decay_fn in {"gaussian", "exponential", "power"}:
            if (not provided.get("beta", False)) and not accept_defaults:
                unit = decay_params_user.get("beta_unit") or WIZARD_DECAY_META.get(decay_fn, {}).get("beta_unit")
                questions.append({
                    "key": "beta",
                    "label": f"beta ({decay_fn})",
                    "type": "number",
                    "default": next((p.get("default") for p in WIZARD_DECAY_META.get(decay_fn, {}).get("params", []) if p.get("key") == "beta"), None),
                    "current": decay_params_user.get("beta"),
                    "unit": unit,
                    "note": "추천 기본값입니다. 필요하면 변경해 주세요.",
                })

    # Region expansion hint (macro-region)
    region_terms = _expand_region_filter_terms(region_filter)

    # Draft payload (not runnable)
    draft = {
        "region_filter": region_filter,
        "region_filter_terms": region_terms,
        "target_subject": target_subject,
        "service_type": service_type,
        "metric_id": metric_id,
        "method": metric.get("tool_method"),
        "static_params": static_params,
        "decay": {"distance_decay_function": decay_fn, **decay_params_user} if decay_fn is not None else None,
        "notes": {
            "exponential_beta_unit_wizard": "1/km (default)",
            "server_exponential_uses": "exp(-beta * d_meters) (beta is 1/m internally)",
        },
    }

    if (not finalize) or (questions and not accept_defaults):
        payload = {
            "status": "PARAM_CONFIRM_REQUIRED",
            "AGENT_INSTRUCTION": (
                "HARD STOP: Show ALL questions below to the user. "
                "Do NOT call wizard_build_job(finalize=True) or run() until the user "
                "explicitly approves in chat (e.g. '이대로 진행', '확인', 'yes'). "
                "Never auto-finalize based on inferred intent."
            ),
            "draft": draft,
            "defaults_used": defaults_used,
            "questions": questions,
            "how_to_finalize": (
                "사용자가 각 파라미터를 확인/수정한 뒤, "
                "wizard_build_job(..., finalize=true)로 다시 호출하세요. "
                "사용자가 '추천값 그대로'라고 하면 accept_defaults=true로 finalize할 수 있습니다."
            ),
            "example_finalize_call": {
                "region_filter": region_filter,
                "target_subject": target_subject,
                "service_type": service_type,
                "metric_id": metric_id,
                "finalize": True,
                "accept_defaults": True,
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    # --- Finalize: Map to run() arguments (internal units) ---
    run_args: Dict[str, Any] = {
        "target_subject": target_subject,
        "region_filter": region_filter,
        "method": metric.get("tool_method"),
    }
    if "threshold_km" in static_params:
        run_args["threshold_km"] = static_params["threshold_km"]
    if "k" in static_params:
        run_args["k"] = static_params["k"]
    if ratio_type is not None:
        run_args["ratio_type"] = ratio_type

    if decay_fn is not None:
        run_args["distance_decay_function"] = decay_fn
        if "beta" in decay_params_internal and decay_params_internal["beta"] is not None:
            run_args["beta"] = decay_params_internal["beta"]
        if "distance_bands_json" in decay_params_internal and decay_params_internal["distance_bands_json"]:
            run_args["distance_bands_json"] = decay_params_internal["distance_bands_json"]

    final_job = {
        **draft,
        "decay_internal": {"distance_decay_function": decay_fn, **decay_params_internal} if decay_fn is not None else None,
        "confirmed": {
            "accept_defaults": bool(accept_defaults),
            "explicitly_provided": provided,
        },
        "defaults_used": defaults_used,
    }

    payload = {"status": "FINAL", "job": final_job, "run_args": run_args}
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool()
def run_job(job_json: str, confirm: bool = False) -> str:
    """Run analysis from a finalized wizard job JSON.

    *** AGENT INSTRUCTIONS ***
    - ALWAYS call this with confirm=False first (default).
      This will show the user a confirmation checklist.
    - Only call with confirm=True AFTER the user has explicitly approved
      in the chat (e.g. typed '이대로 진행', '확인', 'yes').
    - Never auto-set confirm=True based on job content alone.

    - If you pass a payload from wizard_build_job, it must have status == 'FINAL'.
    - For backwards compatibility, you may also pass a raw dict of run-args.
    """
    job = _parse_json_maybe(job_json)
    if not isinstance(job, dict):
        return "job_json은 JSON object여야 합니다."

    if "status" in job:
        if job.get("status") != "FINAL":
            return (
                "아직 파라미터가 확정되지 않았습니다. "
                "wizard_build_job의 결과(status=PARAM_CONFIRM_REQUIRED)에 나온 질문에 답한 뒤 "
                "finalize=true(또는 accept_defaults=true)로 다시 job을 생성해 주세요."
            )
        run_args = job.get("run_args")
    else:
        run_args = job.get("run_args") if "run_args" in job else job

    if not isinstance(run_args, dict):
        return "job_json.run_args가 JSON object여야 합니다."

    allowed = {
        "target_subject","region_filter","method","threshold_km","k","beta",
        "distance_decay_function","decay_function","distance_bands_json","ratio_type",
        "data_dir","file_pop","file_supply","file_shp_unit","file_shp_boundary",
    }
    kwargs = {k: v for k, v in run_args.items() if k in allowed}
    # v7: confirm flag is NEVER inherited from job content.
    # It must be explicitly passed by the agent only after user approval.
    kwargs["confirm"] = bool(confirm)
    return run(**kwargs)  # type: ignore

@mcp.tool()
def data_list_subjects(
    data_dir: Optional[str] = FILE_DIR,
    file_supply: str = FILE_SUPPLY,
    max_items: int = 80,
) -> str:
    """
    List candidate `target_subject` columns from the supply CSV.

    Heuristic:
      - keep numeric columns with a positive total (sum > 0)
      - exclude common identifier / coordinate / text columns
    """
    resolved_data_dir = data_dir or os.environ.get("ACCESS_DATA_DIR") or os.getcwd()
    supply_path = os.path.join(resolved_data_dir, file_supply)
    if not os.path.exists(supply_path):
        return f"공급 CSV 파일이 없습니다: {supply_path}"

    df = pd.read_csv(supply_path)
    exclude_exact = {
        "요양기관번호","기관ID","ID","id",
        "요양기관명","기관명","name",
        "좌표(X)","좌표(Y)","X","Y","lon","lat","longitude","latitude",
        "시도","시군구","읍면동","행정기관",
    }
    num_cols = df.select_dtypes(include=["number"]).columns.tolist()
    candidates = []
    for c in num_cols:
        if c in exclude_exact:
            continue
        s = pd.to_numeric(df[c], errors="coerce").fillna(0)
        total = float(s.sum())
        nonzero = int((s > 0).sum())
        if total > 0 and nonzero > 0:
            candidates.append((c, total, nonzero))
    candidates.sort(key=lambda x: (x[2], x[1]), reverse=True)
    payload = {
        "supply_path": supply_path,
        "candidates": [
            {"column": c, "total": total, "nonzero_rows": nonzero}
            for c, total, nonzero in candidates[: int(max_items)]
        ],
        "note": "target_subject에는 위 column명을 그대로 넣으면 됩니다.",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool()
def data_list_region_prefixes(
    data_dir: Optional[str] = FILE_DIR,
    file_pop: str = FILE_POP,
    max_items: int = 50,
) -> str:
    """
    Suggest region_filter prefixes from the population CSV (based on a name column).

    This is a convenience helper so users can discover valid `region_filter` strings,
    e.g. '대전광역시', '서울특별시', etc.
    """
    resolved_data_dir = data_dir or os.environ.get("ACCESS_DATA_DIR") or os.getcwd()
    pop_path = os.path.join(resolved_data_dir, file_pop)
    if not os.path.exists(pop_path):
        return f"인구 CSV 파일이 없습니다: {pop_path}"

    df = pd.read_csv(pop_path)
    name_col = _first_existing(df.columns, ["행정기관", "ADM_NM", "name", "NAME"])
    if not name_col:
        return f"인구 CSV에서 지역명 컬럼을 찾지 못했습니다. 현재 컬럼: {list(df.columns)[:50]}"

    s = df[name_col].astype(str).fillna("")
    prefixes = s.str.split().str[0].replace({"": np.nan}).dropna()
    counts = prefixes.value_counts().head(int(max_items))
    payload = {
        "pop_path": pop_path,
        "name_col": name_col,
        "prefix_counts": [{"prefix": k, "count": int(v)} for k, v in counts.items()],
        "note": "region_filter는 부분 문자열 매칭입니다. 예: '대전광역시' 또는 '대전' 등",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# =============================================================================
# 4) run (MCP tool)
# =============================================================================

@mcp.tool()
def wizard_get_ui_link(
    region: str | None = None,
    subject: str | None = None,
    service_type: str | None = None,
) -> dict:
    """Return a shareable Web-UI link for the accessibility wizard (if configured).

    Notes:
      - This UI is optional and typically hosted as a published Claude Artifact or a static site.
      - The UI cannot directly call this local MCP server from a normal browser (security/CORS).
        It is intended to help users choose indicators/parameters and then paste the generated
        command/job JSON back into chat.
    """
    if not UI_PUBLIC_URL:
        return {
            "status": "NOT_CONFIGURED",
            "message": "UI_PUBLIC_URL is not configured. Set env var ACCESS_WIZARD_UI_URL to your published UI link.",
        }

    from urllib.parse import urlencode

    params = {}
    if region:
        params["region"] = region
    if subject:
        params["subject"] = subject
    if service_type:
        params["serviceType"] = service_type

    url = UI_PUBLIC_URL
    if params:
        sep = "&" if ("?" in url) else "?"
        url = url + sep + urlencode(params)

    return {"status": "OK", "ui_url": url}

@mcp.tool()
def run(
    target_subject: Optional[str] = None,
    region_filter: Optional[str] = None,
    method: Optional[str] = None,  # v7: no default — must be chosen via wizard or explicit user input
    threshold_km: Optional[float] = None,
    k: Optional[int] = None,
    # distance decay (distance_decay_function) & parameters
    beta: Optional[float] = None,
    distance_decay_function: Optional[str] = None,
    decay_function: Optional[str] = None,  # deprecated alias
    distance_bands_json: Optional[str] = None,
    ratio_type: Optional[str] = None,
    # execution guard
    confirm: bool = False,
    # data paths
    data_dir: Optional[str] = FILE_DIR,
    file_pop: str = FILE_POP,
    file_supply: str = FILE_SUPPLY,
    file_shp_unit: str = FILE_SHP_UNIT,
    file_shp_boundary: str = FILE_SHP_BOUNDARY,
) -> str:
    """
    Orchestrator: load_data -> calculate_accessibility -> visualize_accessibility

    *** AGENT INSTRUCTIONS — READ BEFORE CALLING ***
    1. NEVER call this function with confirm=True on the first attempt.
       Always call with confirm=False first to show the user a confirmation checklist.
    2. NEVER pick method, threshold_km, beta, or distance_decay_function by yourself.
       Use wizard_build_job() to propose parameters and wait for user confirmation.
    3. After returning the confirm prompt, STOP and wait for the user to explicitly
       type approval (e.g. '이대로 진행', '확인', 'yes') in chat.
       Only then call run(..., confirm=True) with the same parameters.
    4. method has NO default value. It must always be explicitly specified.

    Required inputs per method:
      MIN       : target_subject, region_filter
      K_AVG     : + k
      COM       : + threshold_km
      GRAVITY   : + threshold_km, distance_decay_function, (beta if needed)
                   distance_decay_function: 'binary' | 'step' | 'gaussian' | 'exponential' | 'power'
      2SFCA     : + threshold_km
      E2SFCA    : + threshold_km, distance_decay_function, beta (or distance_bands_json for 'step')
                  distance_decay_function: 'step' | 'gaussian' | 'exponential' | 'power' | 'binary'
      PPR       : + threshold_km

    distance_decay_function options (case-insensitive):
      'binary'      — hard cutoff only (default for 2SFCA; E2SFCA/GRAVITY에서도 사용 가능)
      'step'        — requires distance_bands_json
      'gaussian'    — requires beta (sigma in meters, e.g. beta=5000 for 5 km bandwidth)
      'exponential' — requires beta
      'power'       — requires beta
    """
    # v7: method must be explicitly provided
    if not method:
        return (
            "method가 지정되지 않았습니다. "
            "먼저 wizard_build_job()을 사용해 지표와 파라미터를 선택하고 사용자에게 확인받은 뒤 "
            "run()을 호출해 주세요. "
            "가능한 method: MIN, K_AVG, COM, GRAVITY, 2SFCA, E2SFCA, PPR"
        )

    m          = _normalize_method(method)
    OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_results")

    # --- Validate ---
    missing = []
    if not target_subject:
        missing.append("target_subject(예: 산부인과, 내과)")
    if region_filter is None:
        missing.append("region_filter(예: 경상, 서울특별시). 전국 분석은 region_filter='ALL'")
    if region_filter == "ALL":
        region_filter = None

    if m in {"COM", "2SFCA", "E2SFCA", "PPR"} and threshold_km is None:
        missing.append("threshold_km(거리 기준, km)")
    if m == "K_AVG" and k is None:
        missing.append("k(k-nearest 개수)")
    # normalize decay function input (prefer new param)
    df_input = distance_decay_function or decay_function

    if m == "GRAVITY":
        if df_input is None:
            missing.append("distance_decay_function (예: step, gaussian, exponential, power, binary)")
        else:
            df_norm = _normalize_decay_function(df_input)
            if df_norm == "step" and not distance_bands_json:
                missing.append("distance_bands_json (예: [[0,5000,1.0],[5000,10000,0.7],[10000,20000,0.3]])")
            if df_norm in {"gaussian", "exponential", "power"} and beta is None:
                missing.append("beta(감쇠 파라미터)")

    if m == "E2SFCA":
        if df_input is None:
            missing.append("distance_decay_function (예: step, gaussian, exponential, power, binary)")
        else:
            df_norm = _normalize_decay_function(df_input)
            if df_norm == "step" and not distance_bands_json:
                missing.append("distance_bands_json (예: [[0,5000,1.0],[5000,10000,0.7],[10000,20000,0.3]])")
            if df_norm in {"gaussian", "exponential", "power"} and beta is None:
                missing.append("beta(E2SFCA 감쇠 파라미터)")

    if missing:
        return f"다음 정보가 누락되었습니다: {', '.join(missing)}"

    # --- Confirmation guard ---
    if not confirm:
        return _format_confirm_prompt(
            method=method,
            target_subject=target_subject,
            region_filter=region_filter,
            threshold_km=threshold_km,
            k=k,
            distance_decay_function=distance_decay_function or decay_function,
            beta=beta,
            distance_bands_json=distance_bands_json,
            ratio_type=ratio_type,
        )
    
    # --- Data config ---
    resolved_data_dir = data_dir or os.environ.get("ACCESS_DATA_DIR") or os.getcwd()
    resolved_file_shp_unit = (file_shp_unit or FILE_SHP_UNIT)
    resolved_file_shp_boundary = (file_shp_boundary or FILE_SHP_BOUNDARY)
    data_cfg = DataConfig(
        data_dir=resolved_data_dir,
        file_pop=file_pop,
        file_supply=file_supply,
        file_shp_unit=resolved_file_shp_unit,
        file_shp_boundary=resolved_file_shp_boundary,
    )

    # --- Params dict ---
    params: Dict[str, Any] = {"result_col": "accessibility"}

    threshold_m: Optional[float] = None
    if threshold_km is not None:
        threshold_m         = float(threshold_km) * 1000.0
        params["threshold"] = threshold_m

    # --- OD config ---
    od_cfg: Optional[ODConfig] = None
    if m == "MIN":
        od_cfg = ODConfig(mode="euclidean_k_nearest", k=1)
    elif m == "K_AVG":
        od_cfg        = ODConfig(mode="euclidean_k_nearest", k=int(k))
        params["k"]   = int(k)
    elif m in {"COM", "2SFCA", "E2SFCA", "PPR"}:
        od_cfg = ODConfig(mode="euclidean_within", max_cost=threshold_m)
    elif m == "GRAVITY":
        if threshold_m is None:
            return "GRAVITY는 계산량 제어를 위해 threshold_km(=dmax 역할)를 함께 지정하는 것을 권장합니다."
        od_cfg = ODConfig(mode="euclidean_within", max_cost=threshold_m)
        params["dmax"] = threshold_m
        params["distance_decay_function"] = str(df_input) if df_input is not None else "exponential"
        if beta is not None:
            params["beta"] = float(beta)
        if distance_bands_json:
            params["distance_bands"] = _parse_json_maybe(distance_bands_json)
    else:
        return f"지원하지 않는 method입니다: {method}"

    # --- E2SFCA extra params ---
    if m == "E2SFCA":
        params["distance_decay_function"] = str(df_input) if df_input is not None else "binary"
        if distance_bands_json:
            params["distance_bands"] = _parse_json_maybe(distance_bands_json)
        if beta is not None:
            params["beta"] = float(beta)

    # --- PPR extra ---
    if m == "PPR" and ratio_type:
        params["ratio_type"] = str(ratio_type)

    # --- Load ---
    gdf_demand, gdf_supply, od_df, shp_unit, shp_boundary, ctx = load_data(
        target_subject=target_subject,
        region_filter=region_filter,
        data_config=data_cfg,
        column_map=None,
        od_config=od_cfg,
    )

    if gdf_supply.empty:
        return f"알림: '{region_filter}'(또는 전체) 범위에서 '{target_subject}' 공급 데이터가 없습니다."
    if od_df is None or od_df.empty:
        return f"알림: OD 데이터가 비어 있습니다. (threshold={threshold_km}km, method={m})"

    # --- Calculate ---
    gdf_out, gdf_supply_out, info = calculate_accessibility(
        gdf_demand=gdf_demand, gdf_supply=gdf_supply,
        mat_od=od_df, method=m, params=params,
    )

    # --- boundary overlay (optional) ---
    sigungu_gdf = shp_boundary
    if sigungu_gdf is not None and not sigungu_gdf.empty:
        try:
            bbox = _expand_bounds(gdf_out.total_bounds, pad_ratio=0.10)
            sigungu_gdf = sigungu_gdf.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]].copy()
        except Exception:
            pass

    # --- Visualize ---
    title      = f"{region_filter or 'ALL'} / {target_subject} / {m}"
    if threshold_km is not None:
        title += f" / {threshold_km}km"
    if df_input:
        title += f" / {df_input}"

    viz_params               = dict(params)
    viz_params["title"]      = title
    viz_params["output_img"] = f"result_{m}_{region_filter or 'ALL'}_{target_subject}.png"

    summary_md, img_path, embed_img = visualize_accessibility(
        gdf_demand_out=gdf_out, gdf_supply_out=gdf_supply_out,
        mat_od=od_df, method=m, params=viz_params,
        sigungu_gdf=sigungu_gdf, output_dir=OUTPUT_DIR,
    )

    # try:
    #     with open(embed_img, "rb") as _f:
    #         img_embed = f"\n**[결과 지도]** \ndata:image/png;base64,{_b64.b64encode(_f.read()).decode()}\n"
    # except Exception:
    #     img_embed = ""

    # --- Save CSV ---
    csv_filename = f"result_{m}_{(region_filter or 'ALL')}_{target_subject}.csv"
    csv_path = os.path.join(OUTPUT_DIR, csv_filename)
    try:
        csv_cols = [c for c in [DEMAND_ID, DEMAND_NAME, DEMAND_POP, "accessibility"]
                    if c in gdf_out.columns]
        gdf_out[csv_cols].to_csv(csv_path, index=False, encoding="utf-8-sig")
        csv_note = f"\n**[결과 CSV]** `{csv_path}`\n"
    except Exception as e:
        csv_note = f"\n**[CSV 저장 실패]** {e}\n"

    return (
        "✅ 분석 완료!\n\n"
        f"- method: **{m}**\n"
        f"- region: **{region_filter or 'ALL'}**\n"
        f"- subject: **{target_subject}**\n"
        + (f"- threshold: **{threshold_km} km**\n" if threshold_km is not None else "")
        + (f"- distance_decay_function: **{df_input}**\n" if df_input else "")
        + (f"- beta: **{beta}**\n" if beta is not None else "")
        + "\n"
        + summary_md
        + f"\n**[결과 지도 파일]** `{img_path}`\n"
        # + img_embed
        + csv_note
        + f"\n**[데이터/컬럼 매핑 로그]** `{ctx}`\n"
    )


if __name__ == "__main__":
    mcp.run()