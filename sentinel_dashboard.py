import streamlit as st
import pandas as pd
import requests
import os

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Sentinel-Q Control Center", layout="wide")

API_BASE_URL = os.getenv("API_BASE_URL", "http://app:8000")


def api_get_targets() -> list:
    resp = requests.get(f"{API_BASE_URL}/targets", timeout=5)
    resp.raise_for_status()
    return resp.json()


def api_create_target(name: str, url: str, check_interval: int) -> dict:
    payload = {"name": name, "url": url, "check_interval": check_interval, "is_active": True}
    resp = requests.post(f"{API_BASE_URL}/targets", json=payload, timeout=5)
    resp.raise_for_status()
    return resp.json()


def api_get_metrics(target_id: int) -> list:
    resp = requests.get(f"{API_BASE_URL}/metrics/{target_id}", timeout=5)
    resp.raise_for_status()
    return resp.json().get("metrics", [])


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
        api_create_target(new_name, new_url, int(new_interval))
        st.sidebar.success(f"✅ {new_name} guardado.")
        st.rerun()
    except requests.HTTPError as e:
        st.sidebar.error(f"Error al guardar: {e.response.text}")
    except Exception as e:
        st.sidebar.error(f"Error al guardar: {e}")

# --- CUERPO PRINCIPAL ---
st.title("🛡️ Sentinel-Q: Engine Observability")
st.markdown("---")

try:
    targets = api_get_targets()

    if targets:
        target_names = [t["name"] for t in targets]
        selected_target_name = st.selectbox("Selecciona el servicio a inspeccionar:", target_names)
        selected_target = next(t for t in targets if t["name"] == selected_target_name)
        target_id = selected_target["id"]

        metrics_data = api_get_metrics(target_id)

        if metrics_data:
            df = pd.DataFrame(metrics_data)
            df["timestamp"] = pd.to_datetime(df["timestamp"])

            # --- MÉTRICAS SUPERIORES ---
            c1, c2, c3 = st.columns(3)
            avg_lat = df["response_time_ms"].mean()
            uptime = (df[df["status_code"].between(200, 399)].shape[0] / len(df)) * 100

            c1.metric("Latencia Media", f"{avg_lat:.2f} ms")
            c2.metric("Uptime (Últimos 100)", f"{uptime:.1f}%")

            last_status = int(df["status_code"].iloc[-1])
            status_color = "normal" if 200 <= last_status < 400 else "inverse"
            c3.metric("Último Estado", f"HTTP {last_status}", delta_color=status_color)

            # --- GRÁFICA NATIVA (sin Plotly) ---
            df["timestamp"] = df["timestamp"].dt.tz_localize(None)
            st.subheader(f"📈 Telemetría en tiempo real: {selected_target_name}")
            chart_df = df[["timestamp", "response_time_ms"]].set_index("timestamp").sort_index()
            st.line_chart(chart_df, use_container_width=True)

            # Tabla de logs recientes
            with st.expander("Ver logs de eventos detallados"):
                st.table(df.tail(10))
        else:
            st.info(f"📍 El Engine ya está vigilando {selected_target_name}, pero aún no ha guardado el primer latido. Refresca en unos segundos.")
    else:
        st.warning("⚠️ No hay objetivos en vigilancia.")

except Exception as e:
    st.error(f"Error en el Dashboard: {e}")
