import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import date, timedelta
from supabase import create_client, Client
from postgrest.exceptions import APIError

# ✅ Kết nối Supabase
url = st.secrets["supabase_url"]
key = st.secrets["supabase_key"]
supabase: Client = create_client(url, key)

st.set_page_config(page_title="Sales Dashboard", layout="wide")
st.title("📊 Sales Dashboard - Phân tích theo Siêu thị & Sản phẩm")

# ✅ Lấy query parameters an toàn
query_params = st.query_params
user_role = query_params.get("role")
user_zone = query_params.get("zone")
user_area = query_params.get("area")

# ✅ Xử lý list hoặc string
user_role = user_role[0] if isinstance(user_role, list) else user_role
user_zone = user_zone[0] if isinstance(user_zone, list) else user_zone
user_area = user_area[0] if isinstance(user_area, list) else user_area

# ✅ Chuyển "None" thành None thật
try:
    user_zone = int(user_zone)
except (ValueError, TypeError):
    user_zone = None

try:
    user_area = int(user_area)
except (ValueError, TypeError):
    user_area = None

# ✅ Debug xem role và zone/area
with st.sidebar:
    st.markdown("### 🛡️ Thông tin quyền truy cập")
    st.write("👤 Role:", user_role)
    st.write("📍 Zone:", user_zone)
    st.write("🏙️ Area:", user_area)

# ✅ Session state khởi tạo
for key in [
    "sales_df", "zone_list", "area_list",
    "supermarket_list", "product_list", "category_list", "sku_list", "data_loaded", "system"
]:
    if key not in st.session_state:
        st.session_state[key] = None if key == "sales_df" else (
            [] if key != "data_loaded" else False
        )

# ✅ Hàm tải dữ liệu


def fetch_all_data(table_name: str, filters: dict, batch_size=1000):
    all_data = []
    offset = 0
    while True:
        query = supabase.table(table_name).select("*")
        for key, val in filters.items():
            if "_gte" in key:
                col = key.replace("_gte", "")
                query = query.gte(col, val["value"])
            elif "_lte" in key:
                col = key.replace("_lte", "")
                query = query.lte(col, val["value"])
        response = query.range(offset, offset + batch_size - 1).execute()
        data = response.data
        if not data:
            break
        all_data.extend(data)
        offset += batch_size
    return pd.DataFrame(all_data)


# ✅ Bộ lọc thời gian + chế độ xem
with st.sidebar:
    st.markdown("### 🧽 Bộ lọc dữ liệu")
    mode = st.radio("Chế độ xem", ["Doanh số", "Sản phẩm"
                                   ], index=0, horizontal=True)
    view = st.selectbox("Xem theo", ["Ngày", "Tuần", "Tháng"], index=0)

    today = date.today()
    default_start = today.replace(day=1)
    start_date = st.date_input("Từ ngày", default_start)
    end_date = st.date_input("Đến ngày", today)

    st.markdown("---")
    if st.button("📅 Lấy dữ liệu"):
        st.session_state.data_loaded = True
        st.session_state.sales_df = None  # reset lại

# ✅ Tải dữ liệu nếu được yêu cầu
if st.session_state.data_loaded and st.session_state.sales_df is None:
    with st.spinner("Đang tải dữ liệu..."):
        try:
            filters = {
                "report_date_gte": {"op": "gte", "value": str(start_date)},
                "report_date_lte": {"op": "lte", "value": str(end_date)},
            }
            df = fetch_all_data("sales_summary_view", filters)
            if df.empty:
                st.warning("⚠️ Không có dữ liệu.")
                st.session_state.sales_df = None
            else:
                df["report_date"] = pd.to_datetime(df["report_date"])
                df = df[df["product_name"].notnull()]
                st.session_state.sales_df = df
                st.success(f"✅ Dữ liệu đã tải! Tổng cộng {len(df):,} dòng.")
        except APIError as e:
            st.error(f"Lỗi RPC: {e.message}")
        except Exception as e:
            st.error(f"Lỗi không xác định: {e}")

# ✅ Xử lý dữ liệu nếu đã có
if st.session_state.sales_df is not None:
    df = st.session_state.sales_df.copy()

    # ✅ Lọc tự động theo quyền
    if user_role == "TL" and user_zone:
        df = df[df["zone_id"] == user_zone]
    elif user_role == "AD" and user_area:
        df = df[df["area_id"] == user_area]
    elif user_role == "SP":
        pass
    else:
        st.warning("❌ Không đủ quyền truy cập dữ liệu.")
        st.stop()

    # ✅ Nhóm thời gian
    if view == "Ngày":
        df["group"] = df["report_date"].dt.strftime("%d/%m")
    elif view == "Tuần":
        isocal = df["report_date"].dt.isocalendar()
        df["week"] = isocal.week
        df["year"] = isocal.year
        df["week_start"] = df["report_date"] - \
            pd.to_timedelta(df["report_date"].dt.weekday, unit="d")
        df["week_end"] = df["week_start"] + pd.Timedelta(days=6)
        df["group"] = "Tuần " + df["week"].astype(str).str.zfill(
            2) + " (" + df["week_start"].dt.strftime("%d/%m") + "–" + df["week_end"].dt.strftime("%d/%m") + ")"
    else:
        df["month"] = df["report_date"].dt.month
        df["year"] = df["report_date"].dt.year
        df["group"] = "Tháng " + \
            df["month"].astype(str).str.zfill(2) + "/" + df["year"].astype(str)

    # ✅ Cập nhật các danh sách lọc
    st.session_state.zone_list = df["zone_name"].dropna().unique().tolist()
    st.session_state.area_list = df["area_name"].dropna().unique().tolist()
    st.session_state.supermarket_list = df["supermarket_name"].dropna(
    ).unique().tolist()
    st.session_state.product_list = df["product_name"].dropna(
    ).unique().tolist()
    st.session_state.category_list = df["category_name"].dropna(
    ).unique().tolist()
    st.session_state.sku_list = df["sku_name"].dropna().unique().tolist()
    st.session_state.system = df["system"].dropna().unique().tolist()

    # ✅ Sidebar bộ lọc nâng cao
    with st.sidebar:
        st.markdown("### 🎯 Bộ lọc nâng cao")
        min_date = df["report_date"].min().date()
        max_date = df["report_date"].max().date()
        selected_date_range = st.date_input(
            "📆 Chọn khoảng ngày",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date
        )

        filter_zone = st.multiselect("📍 Zone", st.session_state.zone_list)
        filter_area = st.multiselect("🏙️ Khu vực", st.session_state.area_list)
        filter_system = st.multiselect(
            "📦 Hệ thống", st.session_state.system)
        filtered_supermarkets = df[df["zone_name"].isin(filter_zone)]["supermarket_name"].unique().tolist() \
            if filter_zone else st.session_state.supermarket_list
        filter_supermarket = st.multiselect(
            "🏪 Siêu thị", filtered_supermarkets)
        filter_category = st.multiselect(
            "📂 Nhóm sản phẩm", st.session_state.category_list)
        filter_product = st.multiselect(
            "📦 Sản phẩm", st.session_state.product_list)
        filtered_df_for_sku = df[df["product_name"].isin(
            filter_product)] if filter_product else df
        filter_sku = st.multiselect(
            "🔸 Biến thể sản phẩm", filtered_df_for_sku["sku_name"].dropna().unique().tolist())

    # ✅ Áp dụng lọc
    if filter_zone:
        df = df[df["zone_name"].isin(filter_zone)]
    if filter_area:
        df = df[df["area_name"].isin(filter_area)]
    if filter_supermarket:
        df = df[df["supermarket_name"].isin(filter_supermarket)]
    if filter_product:
        df = df[df["product_name"].isin(filter_product)]
    if filter_category:
        df = df[df["category_name"].isin(filter_category)]
    if filter_sku:
        df = df[df["sku_name"].isin(filter_sku)]
    if filter_system:
        df = df[df["system"].isin(filter_system)]
    if selected_date_range:
        start_date, end_date = selected_date_range
        df = df[(df["report_date"] >= pd.to_datetime(start_date)) &
                (df["report_date"] <= pd.to_datetime(end_date))]

    pivot_value = "quantity" if mode == "Sản phẩm" else "total"

    # ✅ Tổng hợp theo Siêu thị
    st.subheader("📌 Tổng hợp theo Siêu thị")
    pivot_main = pd.pivot_table(
        df, values=pivot_value, index="supermarket_name", columns="group", aggfunc="sum", fill_value=0
    )
    pivot_main["TỔNG"] = pivot_main.sum(axis=1)
    pivot_main.loc["TỔNG"] = pivot_main.sum(numeric_only=True)
    st.dataframe(pivot_main.style.format("{:,}"), use_container_width=True)

    # ✅ Tổng hợp theo sản phẩm
    st.subheader("📌 Tổng hợp theo biến thể")
    pivot_main = pd.pivot_table(
        df, values=pivot_value, index="sku_name", columns="group", aggfunc="sum", fill_value=0
    )
    pivot_main["TỔNG"] = pivot_main.sum(axis=1)
    pivot_main.loc["TỔNG"] = pivot_main.sum(numeric_only=True)
    st.dataframe(pivot_main.style.format("{:,}"), use_container_width=True)

    # ✅ Biểu đồ Tổng hợp
    st.subheader("📈 Biểu đồ Tổng hợp")
    chart_data = df.groupby("group")[pivot_value].sum().reset_index()
    fig = px.bar(chart_data, x="group", y=pivot_value,
                 text_auto=True, title=f"Tổng {mode} theo {view.lower()}")
    st.plotly_chart(fig, use_container_width=True)

    # ✅ Tra cứu
    st.subheader("🔍 Tra cứu")
    tab1, tab2, tab3 = st.tabs(
        ["Theo Sản phẩm", "Theo điểm bán", "Theo biến thể"])
    with tab1:
        selected_product = st.selectbox(
            "Chọn sản phẩm", st.session_state.product_list)
        df_filtered = df[df["product_name"] == selected_product]
        pivot = pd.pivot_table(df_filtered, values=pivot_value,
                               index="supermarket_name", columns="group", aggfunc="sum", fill_value=0)
        pivot["TỔNG"] = pivot.sum(axis=1)
        pivot.loc["TỔNG"] = pivot.sum(numeric_only=True)
        st.dataframe(pivot.style.format("{:,}"), use_container_width=True)

    with tab2:
        selected_market = st.selectbox(
            "Theo điểm bán", st.session_state.supermarket_list)
        df_filtered = df[df["supermarket_name"] == selected_market]
        pivot = pd.pivot_table(df_filtered, values=pivot_value,
                               index="product_name", columns="group", aggfunc="sum", fill_value=0)
        pivot["TỔNG"] = pivot.sum(axis=1)
        pivot.loc["TỔNG"] = pivot.sum(numeric_only=True)
        st.dataframe(pivot.style.format("{:,}"), use_container_width=True)

    with tab3:
        selected_sku = st.selectbox(
            "Chọn biến thể", st.session_state.sku_list)
        df_filtered = df[df["sku_name"] == selected_sku]
        pivot = pd.pivot_table(df_filtered, values=pivot_value,
                               index="sku_name", columns="group", aggfunc="sum", fill_value=0)
        pivot["TỔNG"] = pivot.sum(axis=1)
        pivot.loc["TỔNG"] = pivot.sum(numeric_only=True)
        st.dataframe(pivot.style.format("{:,}"), use_container_width=True)

    # ✅ So sánh
    st.subheader("📊 So sánh theo thời gian")
    compare_mode = st.radio(
        "So sánh theo", ["Sản phẩm", "Biến thể sản phẩm"], horizontal=True)
    if compare_mode == "Sản phẩm":
        group_compare = df.groupby(["group", "product_name"])[
            pivot_value].sum().reset_index()
        fig2 = px.line(group_compare, x="group", y=pivot_value, color="product_name",
                       markers=True, title=f"So sánh {mode} theo {view.lower()} theo sản phẩm")
    else:
        group_compare = df.groupby(["group", "sku_name"])[
            pivot_value].sum().reset_index()
        fig2 = px.line(group_compare, x="group", y=pivot_value, color="sku_name",
                       markers=True, title=f"So sánh {mode} theo {view.lower()} theo biến thể sản phẩm")
    st.plotly_chart(fig2, use_container_width=True)
