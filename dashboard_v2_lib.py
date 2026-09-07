# -*- coding: utf-8 -*-
"""
dashboard_v2_lib.py — Thư viện dùng chung cho trang "Dashboard Nâng cao (V2)".

Toàn bộ logic truy xuất dữ liệu, phân quyền và kiểm tra chất lượng số liệu
của trang mới được đặt riêng ở đây. app.py (dashboard cũ) KHÔNG đụng tới file
này nên 2 trang hoàn toàn độc lập và có thể chạy song song để so sánh.

Ghi chú quan trọng về nguồn dữ liệu (đã xác minh bằng probe thực tế):
  * RPC "get_sales_summary" trả về dữ liệu đã gộp sẵn theo (ngày, siêu thị, sku),
    KHÔNG trùng lặp (đã kiểm tra 101.782 dòng / 30 ngày: 0 trùng).
  * PostgREST giới hạn mỗi request tối đa 1.000 dòng -> phải phân trang bằng
    .range(start, end) với batch = 1000 (bằng đúng giới hạn, không được lớn hơn).
  * Bảng gốc sale_items / sales_reports được dùng để "đối chiếu chéo" số liệu.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Optional

import jwt
import pandas as pd
import streamlit as st
from supabase import create_client, Client

try:  # postgrest có thể không import được trong vài môi trường
    from postgrest.exceptions import APIError
except Exception:  # pragma: no cover
    APIError = Exception

# --------------------------------------------------------------------------
# Hằng số
# --------------------------------------------------------------------------
PAGE_SIZE = 1000  # giới hạn tối đa 1 request của PostgREST
SUMMARY_CACHE_TTL = 10 * 60       # 10 phút
DIM_CACHE_TTL = 60 * 60           # 1 giờ cho bảng danh mục
PREV_PERIOD_LABEL = "Kỳ trước"

ROLE_LABELS = {
    "SP": "🔓 Toàn quốc (SP)",
    "TL": "📍 Theo Zone (TL)",
    "AD": "🏙️ Theo Area (AD)",
}


# --------------------------------------------------------------------------
# Kết nối & Token
# --------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def get_client() -> Client:
    """Supabase client dùng chung (cache cấp resource)."""
    return create_client(st.secrets["supabase_url"], st.secrets["supabase_key"])


def get_token() -> Optional[str]:
    """Lấy token từ query string (?token=...) của trang hiện tại."""
    raw = st.query_params.get("token")
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else None
    return str(raw) if raw else None


def _safe_int(value) -> Optional[int]:
    try:
        if value is None or str(value).strip() in ("", "None", "null"):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


@dataclass
class AuthInfo:
    """Kết quả phân quyền cho phiên đăng nhập hiện tại."""
    ok: bool
    role: Optional[str] = None
    zone_id: Optional[int] = None
    area_id: Optional[int] = None
    zone_raw: object = None
    area_raw: object = None
    message: str = ""
    token: Optional[str] = None

    @property
    def scope_label(self) -> str:
        if self.role == "TL":
            return f"Chỉ số liệu Zone id={self.zone_id}"
        if self.role == "AD":
            return f"Chỉ số liệu Area id={self.area_id}"
        if self.role == "SP":
            return "Toàn bộ dữ liệu"
        return "—"


def resolve_auth(token: Optional[str] = None) -> AuthInfo:
    """Giải mã token và kiểm tra vai trò. Không ném lỗi, luôn trả AuthInfo."""
    token = token if token is not None else get_token()
    if not token:
        return AuthInfo(ok=False, message="Không tìm thấy token (thiếu ?token=...).", token=None)
    try:
        payload = jwt.decode(token, st.secrets["SECRET"], algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return AuthInfo(ok=False, message="❌ Token đã hết hạn. Hãy lấy token mới.", token=token)
    except Exception:
        return AuthInfo(ok=False, message="❌ Token không hợp lệ.", token=token)

    role = payload.get("role")
    zone_raw, area_raw = payload.get("zone_id"), payload.get("area_id")
    if role not in ROLE_LABELS:
        return AuthInfo(ok=False, role=str(role), message="Vai trò không được hỗ trợ.",
                        zone_raw=zone_raw, area_raw=area_raw, token=token)
    if role == "TL" and zone_raw in (None, "", "None"):
        return AuthInfo(ok=False, role=role, message="Token TL thiếu zone_id.",
                        zone_raw=zone_raw, area_raw=area_raw, token=token)
    if role == "AD" and area_raw in (None, "", "None"):
        return AuthInfo(ok=False, role=role, message="Token AD thiếu area_id.",
                        zone_raw=zone_raw, area_raw=area_raw, token=token)
    return AuthInfo(
        ok=True, role=role,
        zone_id=_safe_int(zone_raw) if role == "TL" else None,
        area_id=_safe_int(area_raw) if role == "AD" else None,
        zone_raw=zone_raw, area_raw=area_raw, token=token,
    )


# --------------------------------------------------------------------------
# Cache theo session (df không qua pickle để giữ tốc độ, có TTL + nút refresh)
# --------------------------------------------------------------------------
def _cache_bucket() -> dict:
    if "v2_cache" not in st.session_state:
        st.session_state.v2_cache = {}
    return st.session_state.v2_cache


def _cache_key(kind: str, start: date, end: date, zone_id, area_id, reload: int) -> str:
    return f"{kind}|{start}|{end}|{zone_id}|{area_id}|{reload}"


def cache_get(kind: str, start: date, end: date, zone_id, area_id, reload: int) -> Optional[pd.DataFrame]:
    bucket = _cache_bucket()
    key = _cache_key(kind, start, end, zone_id, area_id, reload)
    hit = bucket.get(key)
    if not hit:
        return None
    ts, df = hit
    if time.time() - ts > SUMMARY_CACHE_TTL:
        bucket.pop(key, None)
        return None
    return df


def cache_put(kind: str, start: date, end: date, zone_id, area_id, reload: int, df: pd.DataFrame) -> None:
    bucket = _cache_bucket()
    # giữ tối đa 6 mục để không phình session
    if len(bucket) >= 6:
        oldest = min(bucket, key=lambda k: bucket[k][0])
        bucket.pop(oldest, None)
    key = _cache_key(kind, start, end, zone_id, area_id, reload)
    bucket[key] = (time.time(), df)


# --------------------------------------------------------------------------
# Tải dữ liệu bán hàng (RPC get_sales_summary, có phân trang)
# --------------------------------------------------------------------------
def fetch_sales_summary_pages(start: date, end: date, zone_id=None, area_id=None,
                              progress: Optional[Callable[[int], None]] = None) -> pd.DataFrame:
    """Gọi RPC get_sales_summary theo từng trang 1000 dòng.

    progress(page_no) được gọi sau mỗi trang để hiển thị tiến trình.
    """
    client = get_client()
    params = {"p_start": str(start), "p_end": str(end),
              "p_zone_id": zone_id, "p_area_id": area_id}
    rows_all: list = []
    offset = 0
    page = 0
    while True:
        page += 1
        data = client.rpc("get_sales_summary", params).range(offset, offset + PAGE_SIZE - 1).execute().data
        rows_all.extend(data)
        if progress is not None:
            progress(page)
        if len(data) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        if page > 20_000:  # phòng ngừa vòng lặp vô hạn
            break
    return pd.DataFrame(rows_all)


def load_summary_window(start: date, end: date, zone_id=None, area_id=None,
                        reload: int = 0, show_progress: bool = True) -> pd.DataFrame:
    """Tải dữ liệu 1 khoảng thời gian, có session-cache + thanh tiến trình."""
    if start > end:
        start, end = end, start
    cached = cache_get("summary", start, end, zone_id, area_id, reload)
    if cached is not None:
        return cached

    status = None
    if show_progress:
        status = st.status(f"Đang tải {start} → {end} (trang đầu tiên)...", expanded=False)
    n_pages = [0]

    def _cb(page_no: int) -> None:
        n_pages[0] = page_no
        if status is not None:
            status.update(label=f"Đang tải {start} → {end}… đã lấy {page_no * PAGE_SIZE:,} dòng",
                          state="running")

    t0 = time.perf_counter()
    try:
        df = fetch_sales_summary_pages(start, end, zone_id=zone_id, area_id=area_id, progress=_cb)
    except APIError as exc:
        if status is not None:
            status.update(label="Lỗi khi tải dữ liệu.", state="error")
        raise RuntimeError(f"Lỗi RPC get_sales_summary: {getattr(exc, 'message', exc)}") from exc
    except Exception as exc:
        if status is not None:
            status.update(label="Lỗi khi tải dữ liệu.", state="error")
        raise RuntimeError(f"Lỗi tải dữ liệu: {exc}") from exc

    df = _normalize_summary(df)
    elapsed = time.perf_counter() - t0
    if status is not None:
        status.update(label=f"✅ Xong: {len(df):,} dòng trong {elapsed:.0f}s", state="complete")
    cache_put("summary", start, end, zone_id, area_id, reload, df)
    df.attrs["fetch_seconds"] = elapsed
    return df


def _normalize_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["report_date"] = pd.to_datetime(df["report_date"])
    for col in ("quantity", "total"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    for col in ("zone_id", "area_id", "system_id"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    # loại dòng mất nhận diện sản phẩm (không tính được vào bất kỳ nhóm nào)
    if "product_name" in df.columns:
        df = df[df["product_name"].notna()]
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------
# Bảng danh mục (dimension) — cache dài
# --------------------------------------------------------------------------
@st.cache_data(ttl=DIM_CACHE_TTL, show_spinner=False)
def fetch_dims() -> dict:
    """Tải các bảng danh mục nhỏ để gắn thêm thông tin & kiểm tra chất lượng.

    Trả về dict[str, DataFrame]; bảng nào không truy cập được sẽ là df rỗng.
    """
    client = get_client()
    out: dict = {}

    def _load(name: str, cols: str) -> pd.DataFrame:
        try:
            data = client.table(name).select(cols).execute().data
            return pd.DataFrame(data)
        except Exception:
            return pd.DataFrame()

    out["supermarkets"] = _load("supermarkets",
                                "id,code,name,zone_id,area_id,system_id,is_active")
    out["zones"] = _load("zones", "id,name,area_id")
    out["areas"] = _load("areas", "id,name")
    out["systems"] = _load("systems", "id,name")
    out["product_categories"] = _load("product_categories", "id,name")
    out["products"] = _load("products", "id,code,name,product_category_id")
    out["product_skus"] = _load("product_skus", "id,code,name,product_id,active")
    return out


def dim_store_code_to_id(dims: dict) -> dict:
    sup = dims.get("supermarkets")
    if sup is None or sup.empty or "code" not in sup.columns:
        return {}
    return pd.Series(sup["id"].values, index=sup["code"].astype(str)).to_dict()


def enrich_with_dims(df: pd.DataFrame, dims: dict) -> pd.DataFrame:
    """Gắn cờ is_active + id siêu thị từ bảng supermarkets (nếu khớp code)."""
    if df is None or df.empty:
        return df
    sup = dims.get("supermarkets")
    if sup is None or sup.empty:
        return df
    df = df.copy()
    lookup = sup[["code", "id", "is_active"]].drop_duplicates("code")
    lookup.columns = ["code", "supermarket_id", "supermarket_is_active"]
    df["supermarket_code"] = df["supermarket_code"].astype(str)
    lookup["code"] = lookup["code"].astype(str)
    df = df.merge(lookup, how="left", left_on="supermarket_code", right_on="code")
    df = df.drop(columns=["code"])
    df["supermarket_is_active"] = df["supermarket_is_active"].fillna(True)
    return df


# --------------------------------------------------------------------------
# Bảng sales_reports (phiếu/ca bán) — dùng cho đối chiếu & KPI số phiếu
# --------------------------------------------------------------------------
def fetch_sales_reports_pages(start: date, end: date,
                              supermarket_ids: Optional[list] = None) -> pd.DataFrame:
    """Tải sales_reports theo ngày (tùy chọn lọc theo danh sách id siêu thị)."""
    client = get_client()
    out: list = []
    offset = 0
    while True:
        query = (client.table("sales_reports")
                 .select("id,report_date,supermarket_id,shift,total")
                 .gte("report_date", str(start)).lte("report_date", str(end)))
        if supermarket_ids:
            query = query.in_("supermarket_id", supermarket_ids)
        data = query.range(offset, offset + PAGE_SIZE - 1).execute().data
        out.extend(data)
        if len(data) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        if offset > 1_000_000:
            break
    if not out:
        return pd.DataFrame()
    df = pd.DataFrame(out)
    df["report_date"] = pd.to_datetime(df["report_date"])
    df["total"] = pd.to_numeric(df["total"], errors="coerce").fillna(0)
    return df


def load_sales_reports(start: date, end: date, supermarket_ids: Optional[list],
                       reload: int = 0) -> pd.DataFrame:
    cached = cache_get("reports", start, end, tuple(sorted(supermarket_ids or [])), None, reload)
    if cached is not None:
        return cached
    df = fetch_sales_reports_pages(start, end, supermarket_ids=supermarket_ids)
    cache_put("reports", start, end, tuple(sorted(supermarket_ids or [])), None, reload, df)
    return df


def scope_supermarket_ids(dims: dict, auth: AuthInfo) -> Optional[list]:
    """Danh sách id siêu thị trong phạm vi quyền (None = toàn bộ)."""
    sup = dims.get("supermarkets")
    if sup is None or sup.empty:
        return None
    if auth.role == "TL":
        sup = sup[sup["zone_id"] == auth.zone_id]
    elif auth.role == "AD":
        sup = sup[sup["area_id"] == auth.area_id]
    if sup.empty:
        return []
    return sup["id"].astype(int).tolist()


# --------------------------------------------------------------------------
# Format số liệu (VN)
# --------------------------------------------------------------------------
def fmt_int(x) -> str:
    try:
        return f"{int(round(float(x))):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "—"


def fmt_money(x, compact: bool = True) -> str:
    """Định dạng tiền VNĐ. compact=True: 1,25 tỷ ₫ / 350 tr ₫ ..."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    neg = v < 0
    v = abs(v)
    if compact and v >= 1e9:
        return f"{'-' if neg else ''}{v / 1e9:,.2f} tỷ ₫".replace(",", ".")
    if compact and v >= 1e6:
        return f"{'-' if neg else ''}{v / 1e6:,.1f} tr ₫".replace(",", ".")
    if compact and v >= 1e3:
        return f"{'-' if neg else ''}{v / 1e3:,.0f} k ₫".replace(",", ".")
    return f"{'-' if neg else ''}{v:,.0f} ₫".replace(",", ".")


def fmt_money_full(x) -> str:
    return fmt_int(x) + " ₫"


def pct_delta(cur: float, prev: float) -> Optional[float]:
    """% thay đổi; None nếu không so được."""
    try:
        cur, prev = float(cur), float(prev)
    except (TypeError, ValueError):
        return None
    if prev == 0:
        return None if cur == 0 else float("inf")
    return (cur - prev) / abs(prev) * 100.0


def fmt_pct(x, signed: bool = True) -> str:
    if x is None:
        return "—"
    if x == float("inf"):
        return "mới phát sinh"
    if x == float("-inf"):
        return "—"
    s = f"{x:+.1f}%" if signed else f"{x:.1f}%"
    return s


# --------------------------------------------------------------------------
# Gộp nhóm thời gian
# --------------------------------------------------------------------------
def group_series(df: pd.DataFrame, granularity: str) -> pd.DataFrame:
    """Trả về cột 'group' (nhãn) và 'period_anchor' (datetime để sắp xếp)."""
    df = df.copy()
    d = df["report_date"]
    if granularity == "Ngày":
        df["group"] = d.dt.strftime("%d/%m/%Y")
        df["period_anchor"] = d.dt.normalize()
    elif granularity == "Tuần":
        isocal = d.dt.isocalendar()
        wk_start = d - pd.to_timedelta(d.dt.weekday, unit="d")
        wk_end = wk_start + pd.Timedelta(days=6)
        df["group"] = ("Tuần " + isocal["week"].astype(str).str.zfill(2) + " "
                       + wk_start.dt.strftime("%d/%m") + "–" + wk_end.dt.strftime("%d/%m"))
        df["period_anchor"] = wk_start.dt.normalize()
    else:  # Tháng
        df["group"] = d.dt.strftime("Tháng %m/%Y")
        df["period_anchor"] = d.dt.to_period("M").dt.start_time
    return df


def daily_series(df: pd.DataFrame, value: str = "total", granularity: str = "Ngày") -> pd.DataFrame:
    """Chuỗi thời gian đã gộp theo granularity, index là nhãn theo thứ tự tăng dần."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["group", value])
    g = group_series(df, granularity)
    out = g.groupby(["group", "period_anchor"], as_index=False)[value].sum()
    out = out.sort_values("period_anchor").reset_index(drop=True)
    return out[["group", value]]


def compare_series(cur_df: pd.DataFrame, prev_df: pd.DataFrame,
                   value: str = "total", granularity: str = "Ngày") -> pd.DataFrame:
    """Ghép chuỗi kỳ này với kỳ trước theo đúng thứ tự kỳ (kỳ 1..N)."""
    cur = daily_series(cur_df, value, granularity).reset_index()
    if prev_df is None or prev_df.empty:
        prev = cur.copy()
        prev[value] = 0.0
    else:
        prev = daily_series(prev_df, value, granularity).reset_index()
    n = max(len(cur), len(prev))
    res = pd.DataFrame({
        "period": [f"Kỳ {i + 1}" for i in range(n)],
        "label_cur": cur["group"].tolist() + [""] * (n - len(cur)),
        "cur": cur[value].tolist() + [0.0] * (n - len(cur)),
        "prev": prev[value].tolist() + [0.0] * (n - len(prev)),
    })
    res["delta"] = res["cur"] - res["prev"]
    res["delta_pct"] = res.apply(
        lambda r: pct_delta(r["cur"], r["prev"]), axis=1)
    return res


# --------------------------------------------------------------------------
# Kiểm tra chất lượng dữ liệu
# --------------------------------------------------------------------------
def run_quality_checks(df: pd.DataFrame, dims: dict) -> dict:
    """Bộ kiểm tra số liệu. Trả về dict chứa kết quả dạng text + df chi tiết."""
    res: dict = {"checks": [], "detail_dfs": {}}

    def _add(name: str, ok: bool, message: str):
        res["checks"].append({"name": name, "ok": ok, "message": message})

    if df is None or df.empty:
        _add("Tổng thể", False, "Không có dữ liệu trong khoảng đang chọn.")
        return res

    total_rows = len(df)
    # 1) trùng lặp
    key = ["report_date", "supermarket_code", "sku_code"]
    dup_sub = int(df.duplicated(subset=key).sum())
    dup_full = int(df.duplicated().sum())
    if dup_sub:
        _add("Trùng lặp", False,
             f"{dup_sub:,}/{total_rows:,} dòng trùng theo (ngày, siêu thị, sku); trùng toàn bộ: {dup_full:,}.")
        res["detail_dfs"]["duplicates"] = df[df.duplicated(subset=key, keep=False)]
    else:
        _add("Trùng lặp", True, f"Không có dòng trùng theo (ngày, siêu thị, sku) — {total_rows:,} dòng.")

    # 2) khuyết thiếu nhận diện
    missing = {}
    for col in ("report_date", "supermarket_name", "product_name", "sku_name", "category_name"):
        if col in df.columns:
            missing[col] = int(df[col].isna().sum())
    if any(missing.values()):
        _add("Dữ liệu khuyết", False,
             "Còn thiếu giá trị: " + ", ".join(f"{k}={v:,}" for k, v in missing.items() if v))
        res["detail_dfs"]["missing"] = df[df[[c for c, v in missing.items() if v]].isna().any(axis=1)]
    else:
        _add("Dữ liệu khuyết", True, "Không thiếu trường nhận diện nào.")

    # 3) số liệu bất thường
    neg_total = int((df["total"] < 0).sum())
    neg_qty = int((df["quantity"] < 0).sum())
    zero_total_pos_qty = int(((df["total"] == 0) & (df["quantity"] > 0)).sum())
    if neg_total or neg_qty or zero_total_pos_qty:
        _add("Số liệu bất thường", False,
             f"total<0: {neg_total:,}; quantity<0: {neg_qty:,}; "
             f"total=0 nhưng quantity>0: {zero_total_pos_qty:,}.")
        mask = (df["total"] < 0) | (df["quantity"] < 0) | ((df["total"] == 0) & (df["quantity"] > 0))
        res["detail_dfs"]["abnormal"] = df[mask]
    else:
        _add("Số liệu bất thường", True, "Không có total/quantity âm hay dòng total=0 còn quantity>0.")

    # 4) siêu thị không còn hoạt động (theo danh mục) nhưng vẫn có số liệu
    if "supermarket_is_active" in df.columns:
        inactive = df[df["supermarket_is_active"] == False]  # noqa: E712
        n_inactive = inactive["supermarket_code"].nunique()
        if n_inactive:
            _add("Siêu thị đã ngưng hoạt động", False,
                 f"Có {n_inactive} siêu thị (theo danh mục is_active=False) vẫn xuất hiện trong số liệu "
                 f"({len(inactive):,} dòng, {fmt_money(inactive['total'].sum())}).")
            res["detail_dfs"]["inactive_stores"] = inactive
        else:
            _add("Siêu thị đã ngưng hoạt động", True,
                 "Mọi siêu thị có số liệu đều đang hoạt động trong danh mục.")

    # 5) độ phủ báo cáo của siêu thị
    dates = df["report_date"].dt.normalize().nunique()
    per_store = df.groupby("supermarket_code").agg(
        days=("report_date", "nunique"),
        total=("total", "sum"),
    ).reset_index()
    if dates > 1:
        max_days = per_store["days"].max()
        low = per_store[per_store["days"] < max_days * 0.8] if max_days else pd.DataFrame()
        if not low.empty:
            _add("Độ phủ báo cáo", False,
                 f"{len(low)}/{len(per_store)} siêu thị báo cáo <80% số ngày "
                 f"(cao nhất {max_days}/{dates} ngày). Kiểm tra mục 'Siêu thị' để xem chi tiết.")
            res["detail_dfs"]["low_coverage"] = low.sort_values("days")
        else:
            _add("Độ phủ báo cáo", True,
                 f"Tất cả {len(per_store)} siêu thị báo cáo ≥80% số ngày (tối đa {max_days}/{dates}).")
    return res


def reconcile_reports_vs_summary(df_summary: pd.DataFrame, dims: dict,
                                 start: date, end: date, auth: AuthInfo,
                                 reload: int = 0) -> Optional[dict]:
    """Đối chiếu 2 nguồn: báo cáo phiếu (sales_reports) vs view gộp (RPC).

    Trả về None nếu không tải được sales_reports, ngược lại dict kết quả.
    """
    sup = dims.get("supermarkets")
    if sup is None or sup.empty:
        return None
    store_ids = scope_supermarket_ids(dims, auth)
    rep = load_sales_reports(start, end, store_ids, reload=reload)
    if rep is None or rep.empty:
        return None

    # tổng theo (ngày, siêu thị id)
    rep_day = (rep.groupby(["report_date", "supermarket_id"], as_index=False)["total"].sum())
    rep_day.columns = ["report_date", "supermarket_id", "total_reports"]

    # summary theo (ngày, code) -> gắn id
    sm = df_summary.copy()
    code_map = dim_store_code_to_id(dims)
    sm["supermarket_id"] = sm["supermarket_code"].astype(str).map(
        {str(k): v for k, v in code_map.items()})
    sm_day = (sm.groupby(["report_date", "supermarket_id"], as_index=False)
              .agg(total_view=("total", "sum"), rows=("supermarket_code", "size")))
    sm_day = sm_day.dropna(subset=["supermarket_id"])

    merged = pd.merge(rep_day, sm_day, on=["report_date", "supermarket_id"], how="outer")
    merged["total_reports"] = merged["total_reports"].fillna(0.0)
    merged["total_view"] = merged["total_view"].fillna(0.0)
    merged["diff"] = merged["total_view"] - merged["total_reports"]
    merged["diff_pct"] = merged.apply(
        lambda r: (r["diff"] / r["total_reports"] * 100.0) if r["total_reports"] else None, axis=1)

    name_map = dict(zip(sup["id"].astype(int), sup["name"]))
    merged["supermarket_name"] = merged["supermarket_id"].map(name_map)

    sum_reports = float(rep_day["total_reports"].sum())
    sum_view = float(sm_day["total_view"].sum()) if not sm_day.empty else 0.0
    # lệch lớn: hoặc chênh >0.5% khi cả 2 nguồn đều có, hoặc chỉ 1 nguồn có dữ liệu
    big_mask = merged["diff_pct"].notna() & (merged["diff_pct"].abs() > 0.5)
    one_side = merged["diff_pct"].isna() & (merged["diff"].abs() > 0)
    return {
        "reports_df": merged,
        "sum_reports": sum_reports,
        "sum_view": sum_view,
        "diff": sum_view - sum_reports,
        "n_reports": int(len(rep)),
        "store_days": int(len(merged)),
        "big_mismatch": merged[big_mask | one_side],
    }
