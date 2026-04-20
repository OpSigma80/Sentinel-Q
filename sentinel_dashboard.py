import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
import os

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Sentinel-Q Control Center", layout="wide")

def get_engine():
    db_user = os.getenv('POSTGRES_USER', 'israel_admin')
    db_pass = os.getenv('POSTGRES_PASSWORD')
    db_name = os.getenv('POSTGRES_DB', 'sentinel_db')
    return create_engine(f"postgresql://{db_user}:{db_pass}@sentinel_db_container:5432/{db_name}")

engine = get_engine()

# --- SIDEBAR: GESTIÓN DE OBJETIVOS ---
st.sidebar.header("🛠️ Panel de Control")

with st.sidebar.form("add_target_form"):
    st.subheader("Añadir Nuevo Objetivo")
    new_name = st.text_input("Nombre del Servicio", placeholder="Ej: Mi GitHub")
    new_url = st.text_input("URL de Monitoreo", placeholder="https://github.com/OpSigma80")
    new_interval = st.number_input("Frecuencia (segundos)", min_value=10, value=60)
    submit_button = st.form_submit_button("Registrar en Sentinel")

if submit_button and new_name and new_url:
    try:
        with engine.begin() as conn:
            query = text("""
                INSERT INTO services (name, url, check_interval, is_active) 
                VALUES (:name, :url, :freq, True)
                ON CONFLICT (url) DO UPDATE SET name = EXCLUDED.name;
            """)
            conn.execute(query, {"name": new_name, "url": new_url, "freq": new_interval})
        st.sidebar.success(f"✅ {new_name} guardado.")
    except Exception as e:
        st.sidebar.error(f"Error al guardar: {e}")

# --- CUERPO PRINCIPAL ---
st.title("🛡️ Sentinel-Q: Engine Observability")
st.markdown("---")

try:
    # 1. Obtener lista de objetivos activos desde services (tabla del engine)
    targets_df = pd.read_sql("SELECT id, name FROM services WHERE is_active = True", engine)

    if not targets_df.empty:
        selected_target_name = st.selectbox("Selecciona el servicio a inspeccionar:", targets_df['name'])
        target_id = int(targets_df[targets_df['name'] == selected_target_name]['id'].values[0])

        # 2. Cargar métricas con tipos correctos
        query_metrics = text("""
            SELECT status_code, response_time_ms, timestamp 
            FROM service_metrics 
            WHERE target_id = :tid 
            ORDER BY timestamp DESC 
            LIMIT 100
        """)

        df = pd.read_sql(query_metrics, engine, params={"tid": target_id})

        if not df.empty:
            # --- MÉTRICAS SUPERIORES ---
            c1, c2, c3 = st.columns(3)
            avg_lat = df['response_time_ms'].mean()
            uptime = (df[df['status_code'].between(200, 399)].shape[0] / len(df)) * 100

            c1.metric("Latencia Media", f"{avg_lat:.2f} ms")
            c2.metric("Uptime (Últimos 100)", f"{uptime:.1f}%")

            last_status = int(df['status_code'].iloc[0])
            status_color = "normal" if 200 <= last_status < 400 else "inverse"
            c3.metric("Último Estado", f"HTTP {last_status}", delta_color=status_color)

            # --- GRÁFICA NATIVA (sin Plotly) ---
            df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_localize(None)
            st.subheader(f"📈 Telemetría en tiempo real: {selected_target_name}")
            chart_df = df[['timestamp', 'response_time_ms']].set_index('timestamp').sort_index()
            st.line_chart(chart_df, use_container_width=True)

            # Tabla de logs recientes
            with st.expander("Ver logs de eventos detallados"):
                st.table(df.head(10))
        else:
            st.info(f"📍 El Engine ya está vigilando {selected_target_name}, pero aún no ha guardado el primer latido. Refresca en unos segundos.")
    else:
        st.warning("⚠️ No hay objetivos en vigilancia.")

except Exception as e:
    st.error(f"Error en el Dashboard: {e}")
