# -*- coding: utf-8 -*-
"""
📈 Dashboard Nâng cao (V2) — Sales Insights V2
=============================================
Trang MỚI, độc lập với app.py (Dashboard cũ giữ nguyên 100% để so sánh).
Truy cập: chạy  `streamlit run app.py`  rồi chọn trang này ở sidebar.

Cải tiến so với trang cũ:
  • KPI rõ ràng + so sánh kỳ trước (doanh thu, số lượng, bình quân/ngày, siêu thị…)
  • Nhiều góc nhìn: xu hướng, siêu thị, sản phẩm/SKU, mùa vụ & nhịp bán, tra cứu pivot
  • Kiểm tra chất lượng & đối chiếu chéo 2 nguồn dữ liệu
  • Nhanh hơn: tải 1 lần + cache 10 phút; mọi thao tác lọc chạy local, không gọi lại DB
"""
import sys
from datetime import date, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import dashboard_v2_lib as v2

st.set_page_config(page_title="📈 Dashboard Nâng cao V2", layout="wide")

TODAY = date.today()

# ============================================================ XÁC THỰC
def _auth_ui():
    token = v2.get_token()
    auth = v2.resolve_auth(token)
    if not auth.ok and token is None and st.session_state.get("v2_token"):
        auth = v2.resolve_auth(st.session_state["v2_token"])
    if not auth.ok and token is None and not st.session_state.get("v2_token"):
        with st.sidebar:
            st.markdown("### 🔐 Đăng nhập")
            t = st.text_input("Nhập token (nếu URL chưa có ?token=…)", type="password")
            if t:
                st.session_state["v2_token"] = t
                st.rerun()
    return auth


auth = _auth_ui()

if not auth.ok:
    st.error(auth.message or "Không xác thực được.")
    st.info("Mở đúng cách: `https://<app>/?token=<token>` — hoặc dán token vào ô bên trái.\n\n"
            "Vai trò hỗ trợ: **SP** (toàn quốc), **TL** (theo zone), **AD** (theo area).")
    st.stop()

with st.sidebar:
    st.markdown("### 🛡️ Quyền truy cập")
    st.write("👤 Vai trò:", v2.ROLE_LABELS.get(auth.role, auth.role))
    st.write("🎯 Phạm vi:", auth.scope_label)

st.title("📈 Sales Insights — Dashboard Nâng cao (V2)")
st.caption("Trang **mới**, không sửa app.py cũ. Cùng nguồn RPC `get_sales_summary` nên tổng doanh thu "
           "khớp chính xác với Dashboard cũ khi cùng khoảng thời gian & quyền.")

with st.expander("ℹ️ Cách đọc & so sánh với Dashboard cũ"):
    st.markdown("""
- **Đơn vị**: mọi số tiền là **VND (₫)**. `Doanh thu = Σ total`, `Số lượng = Σ quantity`
  — tương đương cột **TỔNG** của trang cũ (cùng chế độ).
- **Nhanh hơn**: dữ liệu tải **1 lần** mỗi kỳ rồi cache 10 phút; đổi bộ lọc → chạy local, không gọi DB.
- **Chính xác hơn**: tab *🧪 Chất lượng & Đối chiếu* tự kiểm tra trùng lặp / số âm / dữ liệu khuyết /
  siêu thị ngưng hoạt động / độ phủ — và đối chiếu chéo với bảng phiếu `sales_reports`.
- **Kỳ trước** = khoảng cùng độ dài ngay trước kỳ đang chọn; so theo thứ tự kỳ (kỳ 1..N).
""")

# ============================================================ CHỌN KỲ & NÚT TẢI
with st.container(border=True):
    c1, c2, c3 = st.columns([1.15, 1.15, 1.7])
    with c1:
        preset = st.radio(
            "Khoảng thời gian",
            ["Đầu tháng → hôm nay", "7 ngày qua", "14 ngày qua", "30 ngày qua", "Tùy chỉnh"],
            index=0,
            help="'30 ngày qua' lần đầu có thể mất 1–2 phút (cache cho các lần sau).")
    if preset == "Tùy chỉnh":
        with c2:
            start_date = st.date_input("Từ ngày", value=TODAY - timedelta(days=13))
            end_date = st.date_input("Đến ngày", value=TODAY)
    else:
        spans = {"Đầu tháng → hôm nay": 0, "7 ngày qua": 6, "14 ngày qua": 13, "30 ngày qua": 29}
        if preset == "Đầu tháng → hôm nay":
            start_date = TODAY.replace(day=1)
        else:
            start_date = TODAY - timedelta(days=spans[preset])
        end_date = TODAY
        with c2:
            st.caption("📅 Kỳ hiện tại:")
            st.markdown(f"**{start_date.strftime('%d/%m/%Y')} → {end_date.strftime('%d/%m/%Y')}**")
    span_days = (end_date - start_date).days + 1
    with c3:
        compare_prev = st.checkbox("So sánh với kỳ trước (cùng độ dài)", value=True,
                                   help="Tải thêm 1 khoảng ngay trước kỳ đang chọn để tính % tăng/giảm.")
        ba, bb = st.columns(2)
        with ba:
            do_load = st.button("🚀 Tải dữ liệu", type="primary", use_container_width=True)
        with bb:
            if st.button("🔄 Tải lại từ server", use_container_width=True,
                         help="Bỏ cache (session + 10 phút) và tải lại dữ liệu mới nhất"):
                st.session_state["v2_reload"] = int(st.session_state.get("v2_reload", 0)) + 1
                st.session_state["v2_fetch_req"] = True
                st.session_state["v2_loaded"] = False
                st.rerun()
    if span_days > 31:
        st.warning(f"⚠️ Kỳ dài {span_days} ngày — lần tải đầu mất khá lâu (~1s / 1.000 dòng). "
                   "Sau đó được cache nên các thao tác tiếp theo nhanh.")

# ============================================================ TRẠNG THÁI
import traceback  # noqa: E402

_ST = st.session_state
for _k, _v in {"v2_loaded": False, "v2_reload": 0, "v2_main": None,
               "v2_prev": None, "v2_meta": {}, "v2_last_range": None,
               "v2_main_key": None, "v2_prev_key": None, "v2_fetch_req": False}.items():
    if _k not in _ST:
        _ST[_k] = _v

_prev_start = start_date - timedelta(days=span_days)
_prev_end = start_date - timedelta(days=1)

# ============================================================ TẢI DỮ LIỆU
range_changed = _ST["v2_last_range"] != (str(start_date), str(end_date))
if do_load or (_ST["v2_loaded"] and range_changed):
    _ST["v2_fetch_req"] = True  # bấm nút, hoặc đổi kỳ sau khi đã có dữ liệu → tải lại

if _ST["v2_fetch_req"]:
    reload_n = _ST["v2_reload"]
    main_key = (str(start_date), str(end_date), reload_n)
    prev_key = (str(_prev_start), str(_prev_end), reload_n)
    need_main = _ST["v2_main_key"] != main_key
    need_prev = compare_prev and _ST["v2_prev_key"] != prev_key

    if need_main or need_prev:
        try:
            with st.spinner("🔄 Đang chuẩn bị dữ liệu…"):
                dims = v2.fetch_dims()
            if need_main:
                df_main = v2.load_summary_window(
                    start_date, end_date, zone_id=auth.zone_id, area_id=auth.area_id,
                    reload=reload_n)
                fetch_sec = df_main.attrs.get("fetch_seconds", 0.0)
                df_main = v2.enrich_with_dims(df_main, dims)
                _ST["v2_meta"] = {"fetch_seconds": fetch_sec}
            else:
                df_main = _ST["v2_main"]

            if need_prev:
                df_prev = v2.load_summary_window(
                    _prev_start, _prev_end, zone_id=auth.zone_id, area_id=auth.area_id,
                    reload=reload_n)
                prev_sec = df_prev.attrs.get("fetch_seconds", 0.0)
                df_prev = v2.enrich_with_dims(df_prev, dims)
                _ST["v2_meta"]["prev_fetch_seconds"] = prev_sec
            else:
                df_prev = _ST["v2_prev"] if compare_prev else pd.DataFrame()

            if df_main.empty:
                _ST["v2_fetch_req"] = False
                st.warning("⚠️ Không có dữ liệu bán hàng trong kỳ này theo phạm vi quyền của bạn. "
                           "Thử mở rộng thời gian hoặc kiểm tra token.")
                st.stop()

            _ST["v2_main"] = df_main
            _ST["v2_prev"] = df_prev
            _ST["v2_main_key"] = main_key
            _ST["v2_prev_key"] = prev_key if compare_prev else None
            _ST["v2_last_range"] = (str(start_date), str(end_date))
            _ST["v2_loaded"] = True
            _ST["v2_fetch_req"] = False
            st.rerun()
        except RuntimeError as exc:
            _ST["v2_fetch_req"] = False
            st.error(str(exc))
            st.stop()
        except Exception as exc:  # noqa: BLE001
            _ST["v2_fetch_req"] = False
            st.error(f"Lỗi khi tải dữ liệu: {exc}")
            with st.expander("🔧 Chi tiết lỗi (kỹ thuật)"):
                st.code(traceback.format_exc())
            st.stop()
    else:
        _ST["v2_fetch_req"] = False

if _ST["v2_loaded"] and _ST["v2_main"] is not None:
    pass
else:
    st.info("👈 Chọn khoảng thời gian bên trên rồi bấm **🚀 Tải dữ liệu**.\n\n"
            "Lần đầu tải mất 15 giây – 2 phút tuỳ độ dài kỳ; sau đó thao tác lọc gần như tức thì.")
    st.stop()

df_all = _ST["v2_main"]
df_prev_all = _ST["v2_prev"] if compare_prev else pd.DataFrame()
meta = _ST["v2_meta"]

with st.container(border=True):
    m1, m2, m3, m4 = st.columns([1, 1, 1, 2])
    m1.metric("📦 Số dòng dữ liệu", f"{len(df_all):,}".replace(",", "."),
              help="Số dòng (ngày × siêu thị × SKU) trong kỳ")
    m2.metric("🗓️ Ngày có dữ liệu",
              f"{df_all['report_date'].dt.normalize().nunique()}/{span_days}",
              help="Số ngày thực có số liệu trên tổng số ngày của kỳ")
    m3.metric("⏱️ Thời gian tải lần đầu", f"{meta.get('fetch_seconds', 0):.0f}s",
              help="Lần đầu tải từ server; các lần sau dùng cache nên tức thì")
    actual_min = df_all["report_date"].min().date().strftime("%d/%m")
    actual_max = df_all["report_date"].max().date().strftime("%d/%m")
    m4.markdown(
        f"##### Dữ liệu có trong khoảng: **{actual_min} → {actual_max}**\n"
        f"<span style='color:#888'>Phạm vi: {auth.scope_label} · nguồn: RPC `get_sales_summary`</span>",
        unsafe_allow_html=True)

# ============================================================ SIDEBAR: HIỂN THỊ & LỌC
with st.sidebar:
    st.markdown("### 🎛️ Hiển thị")
    mode = st.radio("Chỉ số", ["Doanh số", "Số lượng"], horizontal=True,
                    help="Doanh số = tổng tiền (₫); Số lượng = tổng quantity")
    VALUE = "quantity" if mode == "Số lượng" else "total"
    gran = st.selectbox("Gộp theo", ["Ngày", "Tuần", "Tháng"], index=0)
    top_n = st.slider("Top N hiển thị", 5, 50, 10, 5)

    st.markdown("### 🎯 Bộ lọc nâng cao")
    base = df_all
    z_list = sorted(base["zone_name"].dropna().unique().tolist())
    f_zone = st.multiselect("📍 Zone", z_list)

    pool = base.copy()
    if f_zone:
        pool = pool[pool["zone_name"].isin(f_zone)]
    a_list = sorted(pool["area_name"].dropna().unique().tolist())
    f_area = st.multiselect("🏙️ Khu vực", a_list)
    if f_area:
        pool = pool[pool["area_name"].isin(f_area)]
    sys_list = sorted(pool["system"].dropna().unique().tolist())
    f_sys = st.multiselect("📦 Hệ thống", sys_list)
    if f_sys:
        pool = pool[pool["system"].isin(f_sys)]
    st_list = sorted(pool["supermarket_name"].dropna().unique().tolist())
    f_store = st.multiselect("🏪 Siêu thị", st_list)
    if f_store:
        pool = pool[pool["supermarket_name"].isin(f_store)]

    if "supermarket_is_active" in base.columns:
        only_active = st.checkbox(
            "Chỉ siêu thị đang hoạt động", value=False,
            help="Mặc định TẮT để tổng số khớp chính xác với Dashboard cũ. Bật lên nếu muốn "
                 "loại các siêu thị đã ngưng hoạt động theo danh mục (xem tab Chất lượng).")
    else:
        only_active = False

    cat_list = sorted(pool["category_name"].dropna().unique().tolist())
    f_cat = st.multiselect("📂 Nhóm sản phẩm", cat_list)
    if f_cat:
        pool = pool[pool["category_name"].isin(f_cat)]
    p_list = sorted(pool["product_name"].dropna().unique().tolist())
    f_prod = st.multiselect("📦 Sản phẩm", p_list)
    if f_prod:
        pool = pool[pool["product_name"].isin(f_prod)]
    s_list = sorted(pool["sku_name"].dropna().unique().tolist())
    f_sku = st.multiselect("🔸 Biến thể (SKU)", s_list)


def _apply(d, filters):
    f_zone, f_area, f_sys, f_store, f_cat, f_prod, f_sku, only_active = filters
    if d is None or d.empty:
        return d
    out = d.copy()
    if f_zone:
        out = out[out["zone_name"].isin(f_zone)]
    if f_area:
        out = out[out["area_name"].isin(f_area)]
    if f_sys:
        out = out[out["system"].isin(f_sys)]
    if f_store:
        out = out[out["supermarket_name"].isin(f_store)]
    if f_cat:
        out = out[out["category_name"].isin(f_cat)]
    if f_prod:
        out = out[out["product_name"].isin(f_prod)]
    if f_sku:
        out = out[out["sku_name"].isin(f_sku)]
    if only_active and "supermarket_is_active" in out.columns:
        out = out[out["supermarket_is_active"] == True]  # noqa: E712
    return out


_FILTERS = (f_zone, f_area, f_sys, f_store, f_cat, f_prod, f_sku, only_active)
df = _apply(df_all, _FILTERS)
df_prev = _apply(df_prev_all, _FILTERS)

if df.empty:
    st.warning("Bộ lọc hiện tại không còn dòng dữ liệu nào. Hãy nới lỏng bộ lọc ở sidebar.")
    st.stop()

MONEY = mode == "Doanh số"


# ============================================================ KPI
def _totals(d):
    if d is None or d.empty:
        return None
    return {"total": float(d["total"].sum()),
            "qty": float(d["quantity"].sum()),
            "days": int(d["report_date"].dt.normalize().nunique()),
            "stores": int(d["supermarket_code"].nunique()),
            "products": int(d["product_name"].nunique()),
            "rows": int(len(d))}


cur = _totals(df)
prv = _totals(df_prev)  # None nếu không có kỳ trước


def _delta(cur_v, prev_v):
    """Trả về (delta_display, delta_color) cho st.metric."""
    if prev_v is None:
        return None, "off"
    pct = v2.pct_delta(cur_v, prev_v)
    if pct is None:
        return None, "off"
    if pct == float("inf"):
        return "mới phát sinh", "normal"
    return f"{pct:+.1f}%", ("normal" if pct >= 0 else "inverse")


st.subheader("🎯 Tổng quan KPI")

k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("💰 Doanh thu", v2.fmt_money(cur["total"]),
          *_delta(cur["total"], prv["total"] if prv else None),
          help="Tổng doanh thu kỳ này (so với kỳ trước cùng độ dài)")
k2.metric("🧮 Số lượng bán", v2.fmt_int(cur["qty"]),
          *_delta(cur["qty"], prv["qty"] if prv else None),
          help="Tổng quantity bán ra")
k3.metric("📅 TB doanh thu/ngày",
          v2.fmt_money(cur["total"] / max(cur["days"], 1)),
          *_delta(cur["total"] / max(cur["days"], 1),
                  prv["total"] / max(prv["days"], 1) if prv else None),
          help="Doanh thu chia số ngày thực có dữ liệu")
k4.metric("🏪 Siêu thị phát sinh", v2.fmt_int(cur["stores"]),
          *_delta(cur["stores"], prv["stores"] if prv else None),
          help="Số siêu thị có dòng bán trong kỳ")
k5.metric("📦 Sản phẩm bán được", v2.fmt_int(cur["products"]),
          *_delta(cur["products"], prv["products"] if prv else None),
          help="Số sản phẩm xuất hiện trong kỳ")
_avg = cur["total"] / max(cur["stores"], 1) / max(cur["days"], 1)
_avg_p = (prv["total"] / max(prv["stores"], 1) / max(prv["days"], 1)) if prv else None
k6.metric("🏬 TB/siêu thị/ngày", v2.fmt_money(_avg),
          *_delta(_avg, _avg_p),
          help="Bình quân doanh thu mỗi siêu thị mỗi ngày")

st.caption(f"Trên {cur['rows']:,} dòng (sau bộ lọc hiện tại) — chi tiết ở các tab bên dưới.")

# ============================================================ TABS
tab_trend, tab_store, tab_prod, tab_season, tab_lookup, tab_quality = st.tabs(
    ["📈 Xu hướng & So sánh", "🏪 Phân tích siêu thị", "📦 Sản phẩm & SKU",
     "🗓️ Mùa vụ & nhịp bán", "🔍 Tra cứu chi tiết", "🧪 Chất lượng & Đối chiếu"])

_VAL_FMT = "₫" if MONEY else "đơn vị"
_YMONEY = "₫" if MONEY else None


# ---------------- TAB 1: XU HƯỚNG ----------------
with tab_trend:
    st.subheader(f"Xu hướng {mode.lower()} theo {gran.lower()} — so với kỳ trước")
    cmp = v2.compare_series(df, df_prev, value=VALUE, granularity=gran)
    x_axis = list(range(1, len(cmp) + 1))

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x_axis, y=cmp["cur"], name=f"{mode} (kỳ này)",
        mode="lines+markers", line=dict(width=3, color="#2E86AB"),
        customdata=cmp["label_cur"].to_numpy().reshape(-1, 1),
        hovertemplate="Kỳ %{x} · %{customdata[0]}<br>%{y:,.0f} " + (_YMONEY or "") + "<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=x_axis, y=cmp["prev"], name="Kỳ trước",
        mode="lines", line=dict(width=2, dash="dash", color="#98A1A6"),
        customdata=cmp["label_cur"].to_numpy().reshape(-1, 1),
        hovertemplate="Kỳ %{x} · %{customdata[0]}<br>%{y:,.0f} " + (_YMONEY or "") + "<extra></extra>"))
    fig.update_layout(title=f"{mode} mỗi kỳ (kỳ 1 = ngày đầu kỳ)",
                      xaxis_title="Thứ tự kỳ", yaxis_title=_VAL_FMT,
                      template="plotly_white", legend=dict(orientation="h", y=1.12),
                      hovermode="x unified", margin=dict(l=10, r=10, t=60, b=10))
    st.plotly_chart(fig, use_container_width=True)

    if compare_prev and df_prev is not None and df_prev.empty:
        st.caption("ℹ️ Kỳ trước không có dữ liệu (có thể nằm trước thời điểm bắt đầu thu thập số liệu).")

    bar = go.Figure()
    colors = ["#2E86AB" if v >= 0 else "#C0392B" for v in cmp["delta"]]
    bar.add_trace(go.Bar(x=x_axis, y=cmp["delta"], marker_color=colors,
                         hovertemplate="Kỳ %{x}: %{y:,.0f} " + (_YMONEY or "") + "<extra></extra>"))
    bar.update_layout(title="Chênh lệch từng kỳ so với kỳ trước",
                      xaxis_title="Thứ tự kỳ", yaxis_title=_VAL_FMT,
                      template="plotly_white", margin=dict(l=10, r=10, t=60, b=10))
    st.plotly_chart(bar, use_container_width=True)

    with st.expander("📋 Bảng số liệu xu hướng"):
        tbl = cmp[["period", "label_cur", "cur", "prev", "delta", "delta_pct"]].copy()
        tbl.columns = ["Kỳ", "Nhãn", f"{mode} kỳ này", "Kỳ trước", "Chênh lệch", "%"]
        for c in tbl.columns[2:5]:
            tbl[c] = tbl[c].map(v2.fmt_money if MONEY else v2.fmt_int)
        tbl["%"] = tbl["%"].map(lambda p: v2.fmt_pct(p) if pd.notna(p) else "—")
        st.dataframe(tbl, use_container_width=True, hide_index=True)

# ---------------- TAB 2: SIÊU THỊ ----------------
with tab_store:
    st.subheader(f"🏪 Phân tích siêu thị — {mode.lower()}")
    store_agg = (df.groupby(["supermarket_code", "supermarket_name", "zone_name",
                             "area_name", "system", "supermarket_is_active"], as_index=False)
                 .agg(total=("total", "sum"), qty=("quantity", "sum"),
                      days=("report_date", "nunique")))
    store_agg["value"] = store_agg["qty"] if not MONEY else store_agg["total"]
    store_agg["tb_ngay"] = store_agg["value"] / store_agg["days"].clip(lower=1)

    if df_prev is not None and not df_prev.empty:
        prev_store = (df_prev.groupby(["supermarket_code"], as_index=False)
                      .agg(total_p=("total", "sum"), qty_p=("quantity", "sum")))
        prev_store["value_p"] = prev_store["qty_p"] if not MONEY else prev_store["total_p"]
        store_agg = store_agg.merge(prev_store[["supermarket_code", "value_p"]],
                                    on="supermarket_code", how="left")
        store_agg["delta_pct"] = store_agg.apply(
            lambda r: v2.pct_delta(r["value"], r["value_p"]), axis=1)
    else:
        store_agg["delta_pct"] = np.nan

    store_agg = store_agg.sort_values("value", ascending=False).reset_index(drop=True)
    store_agg["rank"] = store_agg.index + 1
    store_agg["share"] = store_agg["value"] / store_agg["value"].sum() * 100
    store_agg["share_cum"] = store_agg["share"].cumsum()

    ca, cb = st.columns([1.3, 1])
    with ca:
        top_df = store_agg.head(top_n).sort_values("value")
        fig = px.bar(top_df, x="value", y="supermarket_name", orientation="h",
                     color="system",
                     title=f"Top {top_n} siêu thị theo {mode.lower()}")
        fig.update_layout(template="plotly_white",
                          margin=dict(l=10, r=10, t=60, b=10))
        st.plotly_chart(fig, use_container_width=True)
    with cb:
        fig2 = px.scatter(store_agg, x="days", y="tb_ngay", size="value",
                          color="system", hover_name="supermarket_name",
                          title="Số ngày bán × năng suất bình quân/ngày")
        fig2.update_layout(template="plotly_white", showlegend=False,
                           margin=dict(l=10, r=10, t=60, b=10))
        st.plotly_chart(fig2, use_container_width=True)

    with st.expander(f"📋 Bảng xếp hạng đầy đủ ({len(store_agg)} siêu thị)"):
        show = store_agg.copy()
        show["Giá trị"] = show["value"].map(v2.fmt_money if MONEY else v2.fmt_int)
        show["TB/ngày"] = show["tb_ngay"].map(v2.fmt_money if MONEY else v2.fmt_int)
        show["% tăng"] = show["delta_pct"].map(lambda p: v2.fmt_pct(p) if pd.notna(p) else "—")
        show["% thị phần"] = show["share"].map(lambda v: f"{v:.1f}%")
        show["% cộng dồn"] = show["share_cum"].map(lambda v: f"{v:.1f}%")
        show = show.rename(columns={"rank": "Hạng", "supermarket_name": "Siêu thị",
                                    "zone_name": "Zone", "area_name": "Khu vực",
                                    "system": "Hệ thống", "days": "Số ngày bán",
                                    "supermarket_is_active": "Đang hoạt động"})
        cols = ["Hạng", "Siêu thị", "Zone", "Khu vực", "Hệ thống", "Số ngày bán",
                "Giá trị", "TB/ngày", "% tăng", "% thị phần", "% cộng dồn", "Đang hoạt động"]
        st.dataframe(show[cols], use_container_width=True, hide_index=True)
        st.download_button("📥 Tải CSV bảng siêu thị",
                           show[cols].to_csv(index=False).encode("utf-8-sig"),
                           file_name="store_ranking.csv", mime="text/csv")

# ---------------- TAB 3: SẢN PHẨM & SKU ----------------
with tab_prod:
    t3a, t3b, t3c = st.tabs(["Nhóm sản phẩm", "Sản phẩm", "Biến thể (SKU)"])

    def _item_tab(name_col, extra_cols, unit_label):
        agg = (df.groupby([name_col] + extra_cols, as_index=False)
               .agg(total=("total", "sum"), qty=("quantity", "sum"),
                    stores=("supermarket_code", "nunique"), days=("report_date", "nunique")))
        agg["value"] = agg["qty"] if not MONEY else agg["total"]
        if df_prev is not None and not df_prev.empty:
            prev_agg = (df_prev.groupby([name_col], as_index=False)
                        .agg(total_p=("total", "sum"), qty_p=("quantity", "sum")))
            prev_agg["value_p"] = prev_agg["qty_p"] if not MONEY else prev_agg["total_p"]
            agg = agg.merge(prev_agg[[name_col, "value_p"]], on=name_col, how="left")
            agg["delta_pct"] = agg.apply(lambda r: v2.pct_delta(r["value"], r["value_p"]), axis=1)
        else:
            agg["delta_pct"] = np.nan
        agg = agg.sort_values("value", ascending=False).reset_index(drop=True)
        agg["rank"] = agg.index + 1
        agg["share"] = agg["value"] / agg["value"].sum() * 100
        agg["share_cum"] = agg["share"].cumsum()

        ca, cb = st.columns([1.3, 1])
        with ca:
            top = agg.head(top_n).sort_values("value")
            fig = px.bar(top, x="value", y=name_col, orientation="h",
                         color=extra_cols[0] if extra_cols else None,
                         title=f"Top {top_n} {unit_label} theo {mode.lower()}")
            fig.update_layout(template="plotly_white",
                              margin=dict(l=10, r=10, t=60, b=10))
            st.plotly_chart(fig, use_container_width=True)
        with cb:
            head = agg.head(8)
            other = float(agg["value"].iloc[8:].sum()) if len(agg) > 8 else 0.0
            pie_df = pd.concat([head[[name_col, "value"]],
                                pd.DataFrame([{name_col: "Còn lại", "value": other}])],
                               ignore_index=True)
            pie = px.pie(pie_df, names=name_col, values="value",
                         title=f"Thị phần theo {unit_label.lower()} (top 8 + còn lại)")
            pie.update_traces(textposition="inside", textinfo="percent")
            pie.update_layout(template="plotly_white", showlegend=False,
                              margin=dict(l=10, r=10, t=60, b=10))
            st.plotly_chart(pie, use_container_width=True)

        with st.expander(f"📋 Bảng đầy đủ ({len(agg)} {unit_label.lower()})"):
            show = agg.copy()
            show["Giá trị"] = show["value"].map(v2.fmt_money if MONEY else v2.fmt_int)
            show["TB/ngày"] = (show["value"] / show["days"].clip(lower=1)).map(
                v2.fmt_money if MONEY else v2.fmt_int)
            show["% tăng"] = show["delta_pct"].map(lambda p: v2.fmt_pct(p) if pd.notna(p) else "—")
            show["% thị phần"] = show["share"].map(lambda v: f"{v:.1f}%")
            show["% cộng dồn"] = show["share_cum"].map(lambda v: f"{v:.1f}%")
            show = show.rename(columns={"rank": "Hạng", name_col: "Tên",
                                        "stores": "Số siêu thị", "days": "Số ngày bán"})
            keep = (["Hạng", "Tên"] + extra_cols + ["Số siêu thị", "Số ngày bán",
                                                    "Giá trị", "TB/ngày", "% tăng",
                                                    "% thị phần", "% cộng dồn"])
            st.dataframe(show[keep], use_container_width=True, hide_index=True)

    with t3a:
        _item_tab("category_name", [], "nhóm sản phẩm")
    with t3b:
        _item_tab("product_name", ["category_name"], "sản phẩm")
    with t3c:
        _item_tab("sku_name", ["product_name", "category_name"], "biến thể (SKU)")

# ---------------- TAB 4: MÙA VỤ & NHỊP ----------------
with tab_season:
    st.subheader("🗓️ Mùa vụ & nhịp bán")
    d = df.copy()
    d["weekday"] = d["report_date"].dt.dayofweek
    wd_names = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
    wd = (d.groupby("weekday").agg(value=(VALUE, "sum"), days=("report_date", "nunique"))
          .reset_index())
    wd["avg"] = wd["value"] / wd["days"].clip(lower=1)

    ca, cb = st.columns([1, 1.2])
    with ca:
        fig = px.bar(wd, x="weekday", y="avg", color="weekday",
                     labels={"weekday": "", "avg": "TB/ngày"},
                     title=f"TB {mode.lower()} mỗi ngày trong tuần")
        fig.update_layout(xaxis=dict(tickmode="array", tickvals=list(range(7)),
                                     ticktext=wd_names),
                          template="plotly_white", showlegend=False,
                          margin=dict(l=10, r=10, t=60, b=10))
        st.plotly_chart(fig, use_container_width=True)
    with cb:
        iso = d["report_date"].dt.isocalendar()
        d["week_no"] = iso.week
        hm = d.pivot_table(index="week_no", columns="weekday", values=VALUE,
                           aggfunc="sum").reindex(columns=range(7))
        fig2 = go.Figure(data=go.Heatmap(
            z=hm.values, x=wd_names, y=hm.index.astype(str), colorscale="Blues",
            colorbar=dict(title="₫" if MONEY else "sl"),
            hovertemplate="Tuần %{y}, %{x}: %{z:,.0f}<extra></extra>"))
        fig2.update_layout(title=f"{mode} theo tuần × thứ", template="plotly_white",
                           margin=dict(l=10, r=10, t=60, b=10))
        st.plotly_chart(fig2, use_container_width=True)

    wd_sum = d.groupby("weekday")[VALUE].sum()
    top_wd = int(wd_sum.idxmax())
    wk_tot = float(wd_sum.sum())
    with st.container(border=True):
        st.markdown(f"- **Ngày bán mạnh nhất**: thứ **{wd_names[top_wd]}** "
                    f"({v2.fmt_money(wd_sum.max()) if MONEY else v2.fmt_int(wd_sum.max())} cả kỳ).")
        st.markdown(f"- **Số ngày có dữ liệu trong kỳ**: {cur['days']} ngày.")
        if wk_tot:
            weekend = float(wd_sum.reindex([5, 6]).sum())
            st.markdown(f"- **Cuối tuần (T7 + CN)** chiếm **{weekend / wk_tot * 100:.1f}%** "
                        f"{mode.lower()} của kỳ.")

# ---------------- TAB 5: TRA CỨU ----------------
with tab_lookup:
    st.subheader("🔍 Tra cứu chi tiết (pivot theo thời gian)")
    lookup_kind = st.radio("Tra cứu theo", ["Sản phẩm", "Siêu thị", "Biến thể (SKU)"],
                           horizontal=True)
    g = v2.group_series(df, gran)
    if lookup_kind == "Sản phẩm":
        items = sorted(df["product_name"].dropna().unique().tolist())
        sel = st.selectbox("Chọn sản phẩm", items, key="lk_prod")
        sub = g[g["product_name"] == sel]
        idx, idx_name = "supermarket_name", "Siêu thị"
    elif lookup_kind == "Siêu thị":
        items = sorted(df["supermarket_name"].dropna().unique().tolist())
        sel = st.selectbox("Chọn siêu thị", items, key="lk_store")
        sub = g[g["supermarket_name"] == sel]
        idx, idx_name = "product_name", "Sản phẩm"
    else:
        items = sorted(df["sku_name"].dropna().unique().tolist())
        sel = st.selectbox("Chọn biến thể", items, key="lk_sku")
        sub = g[g["sku_name"] == sel]
        idx, idx_name = "supermarket_name", "Siêu thị"

    if sub.empty:
        st.info("Không có dữ liệu cho lựa chọn này trong bộ lọc hiện tại.")
    else:
        order = (g.groupby("group")["period_anchor"].min().sort_values().index.tolist())
        piv = (sub.pivot_table(index=idx, columns="group", values=VALUE,
                               aggfunc="sum", fill_value=0))
        piv["TỔNG"] = piv.sum(axis=1)
        piv = piv.reindex(columns=[c for c in order if c in piv.columns] + ["TỔNG"],
                          fill_value=0)
        piv = piv.sort_values("TỔNG", ascending=False)
        st.dataframe(piv, use_container_width=True)
        st.caption(f"Chỉ số: **{mode}** — mỗi cột là một {gran.lower()} trong kỳ; "
                   "dòng/dạng **TỔNG** = tổng kỳ (so sánh được với trang cũ).")

# ---------------- TAB 6: CHẤT LƯỢNG & ĐỐI CHIẾU ----------------
with tab_quality:
    st.subheader("🧪 Kiểm tra chất lượng & đối chiếu số liệu")
    st.markdown("Kiểm tra chạy trên **toàn bộ dữ liệu tải về** (trước bộ lọc hiển thị, đúng phạm vi "
                "quyền) để không bỏ sót vấn đề. Kết quả chỉ là **cảnh báo** — không tự sửa số liệu.")

    if st.button("🔍 Chạy kiểm tra chất lượng"):
        with st.spinner("Đang kiểm tra…"):
            dims = v2.fetch_dims()
            qr = v2.run_quality_checks(df_all, dims)
        for c in qr["checks"]:
            if c["ok"]:
                st.success(f"✅ **{c['name']}**: {c['message']}")
            else:
                st.warning(f"⚠️ **{c['name']}**: {c['message']}")
        for name, ddf in qr["detail_dfs"].items():
            with st.expander(f"Xem chi tiết — {name} ({len(ddf):,} dòng)"):
                st.dataframe(ddf, use_container_width=True, hide_index=True)
                st.download_button(f"📥 CSV {name}",
                                   ddf.to_csv(index=False).encode("utf-8-sig"),
                                   file_name=f"{name}.csv", mime="text/csv")
    else:
        st.caption("5 nhóm kiểm tra: trùng lặp · dữ liệu khuyết · số âm/bất thường · "
                   "siêu thị ngưng hoạt động · độ phủ báo cáo.")

    st.divider()
    st.markdown("#### 🔄 Đối chiếu chéo 2 nguồn dữ liệu")
    st.markdown("So sánh **tổng doanh thu view gộp (RPC)** với **tổng doanh thu trên các phiếu bán "
                "(`sales_reports`)** cùng kỳ & phạm vi. Lệch nhỏ (<0,5%) thường do ca chưa chốt/cách "
                "ghi nhận; lệch lớn cần kiểm tra từng siêu thị.")

    if st.button("🔄 Tải phiếu bán & đối chiếu"):
        with st.spinner("Đang tải sales_reports (5–15s)…"):
            try:
                dims = v2.fetch_dims()
                rec = v2.reconcile_reports_vs_summary(
                    df_all, dims, start_date, end_date, auth,
                    reload=st.session_state["v2_reload"])
            except RuntimeError as exc:
                st.error(str(exc))
                rec = None
        if rec is None:
            st.warning("Không truy cập được bảng sales_reports (thiếu quyền?) — bỏ qua đối chiếu.")
        else:
            diff_pct = (rec["diff"] / rec["sum_reports"] * 100.0) if rec["sum_reports"] else 0.0
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Doanh thu view (RPC)", v2.fmt_money(rec["sum_view"]))
            col_b.metric("Doanh thu phiếu (sales_reports)", v2.fmt_money(rec["sum_reports"]))
            col_c.metric("Chênh lệch", v2.fmt_money(rec["diff"]),
                         (f"{diff_pct:+.3f}%" if abs(diff_pct) >= 0.001 else "≈0%"),
                         delta_color="normal" if abs(diff_pct) < 0.5 else "inverse")
            st.caption(f"Tổng {rec['n_reports']:,} phiếu / {rec['store_days']:,} lượt siêu thị-ngày trong kỳ.")
            bm = rec["big_mismatch"]
            if bm is not None and not bm.empty:
                st.warning(f"⚠️ {len(bm)} lượt (siêu thị × ngày) lệch >0,5% hoặc chỉ có ở 1 nguồn.")
                show = bm.copy()
                show["diff_pct"] = show["diff_pct"].map(
                    lambda v: "chỉ có view" if pd.isna(v) else f"{v:+.1f}%")
                show["total_reports"] = show["total_reports"].map(v2.fmt_money)
                show["total_view"] = show["total_view"].map(v2.fmt_money)
                show["diff"] = show["diff"].map(v2.fmt_money)
                st.dataframe(show.rename(columns={"report_date": "Ngày",
                                                  "supermarket_id": "Mã siêu thị",
                                                  "supermarket_name": "Siêu thị",
                                                  "total_reports": "Doanh thu phiếu",
                                                  "total_view": "Doanh thu view",
                                                  "diff": "Chênh lệch",
                                                  "diff_pct": "Lệch %"}),
                             use_container_width=True, hide_index=True)
            else:
                st.success("✅ Không có lượt siêu thị-ngày nào lệch >0,5% giữa 2 nguồn.")

    st.divider()
    st.markdown("#### 📤 Xuất dữ liệu đang xem")
    dl_a, dl_b = st.columns(2)
    with dl_a:
        st.download_button("📥 CSV — dữ liệu sau bộ lọc hiển thị",
                           df.to_csv(index=False).encode("utf-8-sig"),
                           file_name="sales_v2_filtered.csv", mime="text/csv")
    with dl_b:
        st.download_button("📥 CSV — dữ liệu gốc (chưa lọc, đúng quyền)",
                           df_all.to_csv(index=False).encode("utf-8-sig"),
                           file_name="sales_v2_raw.csv", mime="text/csv")

st.divider()
st.caption(f"📈 Dashboard V2 · nguồn RPC get_sales_summary · kỳ {start_date} → {end_date} · "
           f"{auth.scope_label} · app.py cũ không bị thay đổi.")
