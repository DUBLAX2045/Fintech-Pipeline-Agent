from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

from src.agent import agent as agent_module


pytestmark = pytest.mark.unit


class _FakeResult:
    def __init__(self, df: pd.DataFrame | None = None, row: tuple | None = None, error: Exception | None = None):
        self._df = df
        self._row = row
        self._error = error

    def fetchdf(self):
        if self._error:
            raise self._error
        return self._df.copy()

    def fetchone(self):
        if self._error:
            raise self._error
        return self._row


class _StaticConn:
    def __init__(self, df: pd.DataFrame, error: Exception | None = None):
        self.df = df
        self.error = error
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return _FakeResult(self.df, error=self.error)


def test_text_helpers_and_ollama_health(requests_mock, monkeypatch):
    monkeypatch.setattr(agent_module, "OLLAMA_BASE_URL", "http://ollama.local")
    monkeypatch.setattr(agent_module, "OLLAMA_MODEL", "llama3.2")
    monkeypatch.setattr(agent_module.time, "sleep", lambda _: None)

    assert agent_module._normalizar_texto("  M\u00e9trica \u00c1gil  ") == "metrica agil"
    assert agent_module._slug_archivo_seguro("Análisis para Campañas / Bogotá") == (
        "analisis_para_campanas_bogota"
    )

    requests_mock.get(
        "http://ollama.local/api/tags",
        json={"models": [{"name": "llama3.2:latest"}]},
    )
    assert agent_module._verificar_ollama(intentos=1) is True

    requests_mock.get("http://ollama.local/api/tags", status_code=503)
    assert agent_module._verificar_ollama(intentos=1) is False

    requests_mock.get("http://ollama.local/api/tags", exc=requests.ConnectionError)
    assert agent_module._verificar_ollama(intentos=1) is False


def test_verificar_ollama_retries_transient_failure(requests_mock, monkeypatch):
    monkeypatch.setattr(agent_module, "OLLAMA_BASE_URL", "http://ollama.local")
    monkeypatch.setattr(agent_module, "OLLAMA_MODEL", "llama3.2")
    monkeypatch.setattr(agent_module.time, "sleep", lambda _: None)
    requests_mock.get(
        "http://ollama.local/api/tags",
        [
            {"status_code": 503},
            {"json": {"models": [{"name": "llama3.2:latest"}]}},
        ],
    )

    assert agent_module._verificar_ollama(intentos=2) is True


@pytest.mark.parametrize(
    ("question", "expected_title", "expected_sql"),
    [
        ("volumen por segmento", "Rentabilidad por segmento", "GROUP BY user_segment"),
        ("volumen por ciudad", "Potencial de crecimiento por ciudad", "GROUP BY city"),
        ("fallos rechazados", "Análisis de fallos por segmento", "failed_transactions"),
        ("top comercios por volumen", "Comercios con mayor potencial de alianza", "top_merchant"),
        ("categoria con mayor volumen", "Categorías con mayor volumen", "top_category"),
        ("canal preferido", "Distribución y rentabilidad por canal", "preferred_channel"),
        ("device preferido", "Distribución por dispositivo", "preferred_device"),
        ("eventos por tipo", "Resumen por tipo de evento", "gold_event_summary"),
        ("metricas diarias por fecha", "Tendencia diaria últimos 35 días", "gold_daily_metrics"),
        ("total del negocio", "KPIs generales del negocio", "total_transacciones"),
    ],
)
def test_sql_por_intencion_covers_business_routes(question, expected_title, expected_sql):
    result = agent_module._sql_por_intencion(question)

    assert result is not None
    sql, title = result
    assert title == expected_title
    assert expected_sql in sql


def test_sql_por_intencion_returns_none_for_summary_or_unknown():
    assert agent_module._sql_por_intencion("resumen ejecutivo") is None
    assert agent_module._sql_por_intencion("hola buenas tardes") is None


def test_ejecutar_sql_duckdb_texto_drops_pii_and_handles_empty(monkeypatch):
    df = pd.DataFrame(
        {
            "user_id": ["u1"],
            "user_email": ["u1@example.com"],
            "total_amount_cop": [1500],
        }
    )
    conn = _StaticConn(df)
    monkeypatch.setattr(agent_module, "_get_conn_duckdb", lambda: conn)

    text = agent_module._ejecutar_sql_duckdb_texto(
        "SELECT user_email, total_amount_cop FROM gold_user_360"
    )

    assert "total_amount_cop" in text
    assert "u1@example.com" not in text
    assert "Advertencia PII" in text
    assert "LIMIT 100" in conn.executed[0][0]

    monkeypatch.setattr(agent_module, "_get_conn_duckdb", lambda: _StaticConn(pd.DataFrame()))
    assert agent_module._ejecutar_sql_duckdb_texto("SELECT * FROM gold_user_360") == (
        "La consulta no retorno resultados."
    )


def test_respuesta_desde_sql_and_tool_parser(monkeypatch):
    monkeypatch.setattr(agent_module, "_ejecutar_sql_duckdb_texto", lambda sql, max_rows=100: "fila real")

    response = agent_module._respuesta_desde_sql("Titulo", "SELECT 1")

    assert "fila real" in response
    assert agent_module._extraer_tool_call("sin json") is None


def test_modo_respuesta_claro_changes_ollama_prompt(requests_mock, monkeypatch):
    monkeypatch.setattr(agent_module, "OLLAMA_BASE_URL", "http://ollama.local")
    monkeypatch.setattr(agent_module, "OLLAMA_MODEL", "llama3.2")
    monkeypatch.setattr(agent_module, "_verificar_ollama", lambda: True)
    requests_mock.post(
        "http://ollama.local/api/chat",
        json={"message": {"content": "**Resumen Ejecutivo**\nExplicación simple."}},
    )

    response = agent_module._interpretar_con_ollama(
        "Dame el resumen ejecutivo",
        "usuarios 489\nvolumen 211300000",
        modo_respuesta="claro",
    )
    payload = requests_mock.last_request.json()

    assert agent_module.normalizar_modo_respuesta("persona natural") == "claro"
    assert "NO son expertas" in payload["messages"][0]["content"]
    assert "ejemplos cotidianos" in payload["messages"][-1]["content"]
    assert "explicación clara" in response


def test_grafico_contexto_deterministico_corrige_minimo_y_tipo(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_module, "_get_charts_dir", lambda: tmp_path)
    monkeypatch.setattr(agent_module, "_verificar_ollama", lambda: False)
    monkeypatch.setattr(agent_module, "_periodo_gold_global", lambda: "2026-04-30 a 2026-06-03")
    df = pd.DataFrame(
        {
            "city": ["Bogotá", "Cartagena", "Barranquilla", "Medellín", "Cali"],
            "revenue_por_usuario": [483785.0, 442937.0, 421751.0, 417489.0, 402195.0],
        }
    )

    response = agent_module._ejecutar_grafico_con_analisis(
        df,
        "Revenue por Ciudad",
        "line",
        "Me puedes generar un grafico de lineas de los tickets por ciudad",
        "claro",
    )

    assert "Datos usados para responder" in response
    assert "Verificacion simple de los datos" in response
    assert "dinero promedio movido por persona" in response
    assert "Bogotá" in response
    assert "Cali" in response
    assert "Analisis por datos" in response
    assert "Conclusion" in response
    assert "Recomendacion" in response
    contexto = agent_module._contexto_estructurado_actual()
    assert contexto is not None
    assert contexto["tipo_usado"] == "bar"


def test_grafico_reemplaza_ollama_contradictorio_por_control_deterministico(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_module, "_get_charts_dir", lambda: tmp_path)
    monkeypatch.setattr(agent_module, "_periodo_gold_global", lambda: "2026-04-30 a 2026-06-03")
    monkeypatch.setattr(
        agent_module,
        "_analizar_grafico_con_ollama",
        lambda *args, **kwargs: (
            "**Analisis por datos**\n"
            "La tasa de fallo tiene como menor valor a Bogotá y como mayor valor a Cali. "
            "Este texto es deliberadamente largo para simular una respuesta completa de Ollama. "
            "Repite una lectura de negocio suficiente para pasar el umbral de longitud, pero contradice "
            "los hechos validados por codigo en la metrica de tasa de fallo. "
            "**Conclusion**\n"
            "La ciudad con mejor comportamiento seria Bogotá, aunque el dato real dice otra cosa. "
            "Este bloque mantiene extension adicional para evitar que la validacion lo descarte por corto. "
            "**Recomendacion**\n"
            "Invertir en Bogotá por menor friccion, recomendacion que debe ser reemplazada por control."
        ),
    )
    df = pd.DataFrame(
        {
            "city": ["Bogotá", "Medellín", "Cali"],
            "tasa_fallo_pct": [27.3, 19.6, 23.9],
            "usuarios": [81, 121, 81],
        }
    )

    response = agent_module._ejecutar_grafico_con_analisis(
        df,
        "Potencial de crecimiento por ciudad",
        "pie",
        "Que ciudad tiene mayor potencial de crecimiento?",
        "claro",
    )

    assert "Respuesta en lenguaje claro" in response
    assert "porcentaje de operaciones con problemas" in response
    assert "Bogotá" in response
    assert "Medellín" in response
    assert "Invertir en Bogotá por menor friccion" not in response


def test_grafico_respaldo_deterministico_cuando_ollama_responde_corto(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_module, "_get_charts_dir", lambda: tmp_path)
    monkeypatch.setattr(agent_module, "_periodo_gold_global", lambda: "2026-04-30 a 2026-06-03")
    monkeypatch.setattr(agent_module, "_analizar_grafico_con_ollama", lambda *args, **kwargs: "Respuesta corta")
    df = pd.DataFrame(
        {
            "user_segment": ["student", "family"],
            "usuarios": [146, 108],
            "inactivos": [34, 20],
        }
    )

    response = agent_module._ejecutar_grafico_con_analisis(
        df,
        "Analisis para Estrategia de Campanas por Segmento",
        "pie",
        "Que campana lanzarias este mes?",
        "claro",
    )

    assert "Analisis por datos" in response
    assert "reactivacion para estudiantes" in response
    assert "personas que llevan tiempo sin usar la plataforma" in response
    assert "Respuesta corta" not in response


def test_agent_query_prioriza_seguimiento_contextual_sobre_grafico(monkeypatch):
    agent_module.reset_agent()
    agent_module._actualizar_contexto_estructurado(
        {
            "tipo": "grafico",
            "titulo": "Revenue por Ciudad",
            "pregunta": "grafico de tickets por ciudad",
            "datos_texto": "city revenue_por_usuario\nBogotá 483785\nCali 402195",
            "hechos_texto": "- Métrica principal 'revenue_por_usuario': mínimo Cali = 402,195.",
        }
    )
    monkeypatch.setattr(agent_module, "_verificar_ollama", lambda: False)
    monkeypatch.setattr(
        agent_module,
        "_manejar_peticion_grafico",
        lambda pregunta, modo_respuesta="profesional": "NO_DEBE_GRAFICAR",
    )

    response = agent_module.agent_query(
        "oye pero te estas equivocando, esta grafica indica que el valor mas bajo es Cali; "
        "estos valores desde que tiempos salen?",
        modo_respuesta="claro",
    )

    assert "Retomando la respuesta anterior" in response
    assert "mínimo Cali" in response
    assert "NO_DEBE_GRAFICAR" not in response


def test_agent_query_elabora_recomendaciones_sin_repetir_formato(monkeypatch):
    agent_module.reset_agent()
    agent_module._actualizar_contexto_estructurado(
        {
            "tipo": "grafico",
            "titulo": "Analisis para Estrategia de Campanas por Segmento",
            "pregunta": "Que campana lanzarias este mes?",
            "datos_texto": "segmento usuarios inactivos tasa_fallo revenue",
            "hechos_texto": "- Respuesta basada en segmentos Gold.",
            "metricas": [
                {
                    "columna": "inactivos",
                    "nombre": "usuarios inactivos",
                    "max_label": "student",
                    "min_label": "young_professional",
                    "max_val": 34,
                    "min_val": 19,
                    "brecha": 15,
                    "brecha_txt": "78.9% sobre el mínimo",
                },
                {
                    "columna": "tasa_fallo_pct",
                    "nombre": "tasa de fallo",
                    "max_label": "family",
                    "min_label": "premium",
                    "max_val": 26.9,
                    "min_val": 16.4,
                    "brecha": 10.5,
                    "brecha_txt": "64.0% sobre el mínimo",
                },
                {
                    "columna": "revenue_por_usuario",
                    "nombre": "revenue por usuario",
                    "max_label": "young_professional",
                    "min_label": "student",
                    "max_val": 439775,
                    "min_val": 422550,
                    "brecha": 17225,
                    "brecha_txt": "4.1% sobre el mínimo",
                },
            ],
        }
    )
    monkeypatch.setattr(agent_module, "_verificar_ollama", lambda: False)
    monkeypatch.setattr(
        agent_module,
        "_respuesta_datos_confiable",
        lambda *args, **kwargs: "NO_DEBE_REPETIR_ANALISIS",
    )

    response = agent_module.agent_query(
        "oye, a partir de esas recomendaciones dame 3 ideas especificas bien ejecutadas",
        modo_respuesta="claro",
    )

    assert "Campaña `Vuelve y gana`" in response
    assert "Campaña `Operación sin fricción`" in response
    assert "Campaña `Más valor por uso frecuente`" in response
    assert "estudiantes" in response
    assert "NO_DEBE_REPETIR_ANALISIS" not in response
    assert "Analisis por datos" not in response


def test_seguimiento_elaboracion_descarta_formato_repetido_de_ollama(monkeypatch, requests_mock):
    agent_module.reset_agent()
    agent_module._actualizar_contexto_estructurado(
        {
            "tipo": "grafico",
            "titulo": "Analisis para Estrategia de Campanas por Segmento",
            "pregunta": "Que campana lanzarias este mes?",
            "datos_texto": "segmento usuarios inactivos",
            "hechos_texto": "- Usuarios inactivos: máximo student = 34.",
            "metricas": [
                {
                    "columna": "inactivos",
                    "nombre": "usuarios inactivos",
                    "max_label": "student",
                    "min_label": "young_professional",
                    "max_val": 34,
                    "min_val": 19,
                    "brecha": 15,
                    "brecha_txt": "78.9% sobre el mínimo",
                }
            ],
        }
    )
    monkeypatch.setattr(agent_module, "OLLAMA_BASE_URL", "http://ollama.local")
    monkeypatch.setattr(agent_module, "_verificar_ollama", lambda: True)
    requests_mock.post(
        "http://ollama.local/api/chat",
        json={"message": {"content": "Verificacion simple\n\nAnalisis por datos\nRespuesta repetida."}},
    )

    response = agent_module.agent_query(
        "dame 3 recomendaciones especificas implementadas sobre esa recomendacion",
        modo_respuesta="claro",
    )

    assert "Vuelve y gana" in response
    assert "Verificacion simple" not in response


def test_seguimiento_generico_usa_prompt_contextual_flexible(monkeypatch, requests_mock):
    agent_module.reset_agent()
    agent_module._actualizar_contexto_estructurado(
        {
            "tipo": "grafico",
            "titulo": "Campanas por segmento",
            "pregunta": "Que campana lanzarias este mes?",
            "datos_texto": "segmento inactivos\nstudent 34\nyoung_professional 19",
            "hechos_texto": "- Usuarios inactivos: maximo student = 34.",
            "ultima_respuesta": "Idea 1: reactivar estudiantes. Idea 2: reducir friccion operativa.",
        }
    )
    monkeypatch.setattr(agent_module, "OLLAMA_BASE_URL", "http://ollama.local")
    monkeypatch.setattr(agent_module, "_verificar_ollama", lambda: True)
    requests_mock.post(
        "http://ollama.local/api/chat",
        json={"message": {"content": "Mensaje listo para la segunda idea, sin repetir el analisis."}},
    )

    response = agent_module.agent_query(
        "redacta un mensaje para la segunda idea",
        modo_respuesta="claro",
    )
    payload = requests_mock.last_request.json()
    system_prompt = payload["messages"][0]["content"]
    context_message = " ".join(m["content"] for m in payload["messages"] if m["role"] == "system")

    assert "Mensaje listo" in response
    assert "Adapta el formato" in system_prompt
    assert "FORMATO OBLIGATORIO" not in system_prompt
    assert "Ultima respuesta del agente" in context_message
    assert "Idea 2: reducir friccion operativa" in context_message


def test_consulta_nueva_de_kpis_no_se_confunde_con_seguimiento(monkeypatch):
    agent_module.reset_agent()
    agent_module._actualizar_contexto_estructurado(
        {
            "tipo": "grafico",
            "titulo": "Campanas por segmento",
            "pregunta": "Que campana lanzarias este mes?",
            "hechos_texto": "- Usuarios inactivos: maximo student = 34.",
        }
    )
    monkeypatch.setattr(
        agent_module,
        "_resolver_seguimiento_con_ollama",
        lambda *args, **kwargs: "NO_DEBE_SEGUIR_CONTEXTO",
    )
    monkeypatch.setattr(
        agent_module,
        "_respuesta_datos_confiable",
        lambda *args, **kwargs: "KPIS_OK",
    )

    response = agent_module.agent_query("dame el resumen ejecutivo de la plataforma", modo_respuesta="claro")

    assert response == "KPIS_OK"
    assert response != "NO_DEBE_SEGUIR_CONTEXTO"


def test_seguimiento_generico_sin_ollama_no_repite_consulta_gold(monkeypatch):
    agent_module.reset_agent()
    agent_module._actualizar_contexto_estructurado(
        {
            "tipo": "grafico",
            "titulo": "Campanas por segmento",
            "pregunta": "Que campana lanzarias este mes?",
            "datos_texto": "segmento inactivos\nstudent 34\nyoung_professional 19",
            "hechos_texto": "- Usuarios inactivos: maximo student = 34.",
            "metricas": [
                {
                    "columna": "inactivos",
                    "nombre": "usuarios inactivos",
                    "max_label": "student",
                    "min_label": "young_professional",
                    "max_val": 34,
                    "min_val": 19,
                    "brecha": 15,
                    "brecha_txt": "78.9% sobre el minimo",
                }
            ],
        }
    )
    monkeypatch.setattr(agent_module, "_verificar_ollama", lambda: False)
    monkeypatch.setattr(
        agent_module,
        "_respuesta_datos_confiable",
        lambda *args, **kwargs: "NO_DEBE_CONSULTAR_GOLD_NUEVO",
    )

    response = agent_module.agent_query(
        "hazlo mas claro y dame dos alternativas",
        modo_respuesta="claro",
    )

    assert "NO_DEBE_CONSULTAR_GOLD_NUEVO" not in response
    assert "Vuelve y gana" in response or "Retomando" in response


def test_seguimiento_sin_ollama_enfoca_ordinal_y_alternativas(monkeypatch):
    agent_module.reset_agent()
    agent_module._actualizar_contexto_estructurado(
        {
            "tipo": "grafico",
            "titulo": "Campanas por segmento",
            "pregunta": "Que campana lanzarias este mes?",
            "datos_texto": "segmento inactivos tasa_fallo_pct\nstudent 34 26.8\nfamily 20 26.9",
            "hechos_texto": "- Tasa de fallo: maximo family = 26.90.",
            "metricas": [
                {
                    "columna": "tasa_fallo_pct",
                    "nombre": "tasa de fallo",
                    "max_label": "family",
                    "min_label": "premium",
                    "max_val": 26.9,
                    "min_val": 16.4,
                    "brecha": 10.5,
                    "brecha_txt": "64.0% sobre el minimo",
                }
            ],
        }
    )
    monkeypatch.setattr(agent_module, "_verificar_ollama", lambda: False)

    response = agent_module.agent_query(
        "dame dos alternativas para la segunda idea",
        modo_respuesta="claro",
    )

    assert "Dos alternativas" in response
    assert "familias" in response
    assert "friccion" in response.lower()
    assert "Analisis por datos" not in response


def test_gold_profile_and_preconsulta_controls(monkeypatch):
    df = pd.DataFrame(
        {
            "user_id": ["u1", "u2"],
            "city": ["Bogotá", "Cali"],
            "total_amount_cop": [1000.0, 2000.0],
            "last_transaction_date": pd.to_datetime(["2026-06-01", "2026-06-02"]),
            "user_email": ["a@test.com", "b@test.com"],
        }
    )

    perfil = agent_module._perfilar_dataframe_gold("gold_user_360", df)

    assert "total_amount_cop" in perfil["metricas"]
    assert "city" in perfil["dimensiones"]
    assert "last_transaction_date" in perfil["fechas"]
    assert "user_email" in perfil["pii"]

    respuesta, ruta = agent_module._evaluar_control_preconsulta("muéstrame correos de usuarios")
    assert ruta == "bloqueo_pii"
    assert "datos personales" in respuesta

    agent_module.reset_agent()
    respuesta, ruta = agent_module._evaluar_control_preconsulta("como vamos")
    assert ruta == "aclaracion_ambigua"
    assert "Necesito una aclaración" in respuesta

    respuesta, ruta = agent_module._evaluar_control_preconsulta("predice exactamente el proximo mes")
    assert ruta == "prediccion_no_soportada"
    assert "predicción exacta" in respuesta


def test_hallucination_guard_appends_deterministic_correction():
    contexto = {
        "tipo": "grafico",
        "tipo_solicitado": "line",
        "tipo_usado": "bar",
        "etiquetas": ["Bogotá", "Barranquilla", "Cali"],
        "hechos_texto": (
            "- Métrica principal 'revenue_por_usuario': máximo Bogotá = 483,785.\n"
            "- Métrica principal 'revenue_por_usuario': mínimo Cali = 402,195."
        ),
    }
    texto = "El valor más bajo es Barranquilla y corresponde a una línea temporal."

    validado = agent_module._validar_respuesta_contra_contexto(texto, contexto)

    assert "Control determinístico" in validado
    assert "mínimo validado por código es **Cali**" in validado
    assert "se usaron **barras**" in validado


def test_agent_trace_logger_writes_jsonl(monkeypatch, tmp_path):
    trace_path = tmp_path / "agent_traces.jsonl"
    monkeypatch.setattr(agent_module, "_TRACE_PATH", trace_path)

    agent_module._registrar_traza_agente(
        "pregunta",
        "respuesta",
        "ruta_test",
        "claro",
        {"detalle": "ok"},
    )

    payload = trace_path.read_text(encoding="utf-8").strip()
    assert '"ruta": "ruta_test"' in payload
    assert '"modo_respuesta": "claro"' in payload


def test_consultar_sql_security_databricks_and_duckdb(monkeypatch):
    from src.config import databricks_config as db

    blocked = agent_module.consultar_sql("DROP TABLE gold_user_360")
    assert "Consulta bloqueada" in blocked

    monkeypatch.setattr(db, "_validar_credenciales", lambda: (True, "OK"))
    monkeypatch.setattr(db, "ejecutar_query", lambda sql, max_filas=100: [{"metric": "usuarios", "valor": 2}])
    databricks_text = agent_module.consultar_sql("SELECT COUNT(*) AS valor FROM gold_user_360")
    assert "usuarios" in databricks_text
    assert "valor" in databricks_text

    monkeypatch.setattr(db, "ejecutar_query", lambda sql, max_filas=100: [])
    assert "resultados" in agent_module.consultar_sql("SELECT * FROM gold_user_360")

    monkeypatch.setattr(db, "_validar_credenciales", lambda: (False, "faltan"))
    monkeypatch.setattr(
        agent_module,
        "_get_conn_duckdb",
        lambda: _StaticConn(pd.DataFrame({"user_name": ["Ana"], "valor": [10]})),
    )
    fallback_text = agent_module.consultar_sql("SELECT user_name, valor FROM gold_user_360")
    assert "valor" in fallback_text
    assert "Ana" not in fallback_text

    monkeypatch.setattr(
        agent_module,
        "_get_conn_duckdb",
        lambda: _StaticConn(pd.DataFrame(), error=RuntimeError("duckdb roto")),
    )
    assert "Error SQL" in agent_module.consultar_sql("SELECT * FROM gold_user_360")


def test_consultar_databricks_routes(monkeypatch):
    from src.config import databricks_config as db

    assert "SQL bloqueado" in agent_module.consultar_databricks("DELETE FROM t")

    monkeypatch.setattr(db, "_validar_credenciales", lambda: (False, "faltan variables"))
    missing = agent_module.consultar_databricks("SELECT 1")
    assert "Databricks no configurado" in missing

    monkeypatch.setattr(db, "_validar_credenciales", lambda: (True, "OK"))
    monkeypatch.setattr(db, "ejecutar_query", lambda sql, max_filas=100: [{"answer": 1}])
    assert "answer" in agent_module.consultar_databricks("SELECT 1")

    monkeypatch.setattr(db, "ejecutar_query", lambda sql, max_filas=100: [])
    assert "resultados" in agent_module.consultar_databricks("SELECT 1")

    monkeypatch.setattr(db, "ejecutar_query", lambda sql, max_filas=100: (_ for _ in ()).throw(RuntimeError("timeout")))
    assert "Error Databricks" in agent_module.consultar_databricks("SELECT 1")


def test_chart_tools_profile_and_summary(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_module, "_get_charts_dir", lambda: tmp_path)
    chart_df = pd.DataFrame({"segmento": ["premium", "student"], "valor": [10, 5]})
    monkeypatch.setattr(agent_module, "_get_conn_duckdb", lambda: _StaticConn(chart_df))

    assert "guardado" in agent_module.grafico_barras("SELECT segmento, valor FROM t", "Barras")
    assert "guardado" in agent_module.grafico_tendencia_diaria("SELECT dia, valor FROM t", "Linea")
    assert "guardado" in agent_module.grafico_segmentos("SELECT segmento, valor FROM t", "Pie")

    monkeypatch.setattr(agent_module, "_get_conn_duckdb", lambda: _StaticConn(pd.DataFrame()))
    assert "Sin datos" in agent_module.grafico_barras("SELECT * FROM t", "Vacio")

    monkeypatch.setattr(
        agent_module,
        "_get_conn_duckdb",
        lambda: _StaticConn(pd.DataFrame(), error=RuntimeError("consulta rota")),
    )
    assert "Error:" in agent_module.grafico_segmentos("SELECT * FROM t", "Error")

    user_df = pd.DataFrame({"user_id": ["u1"], "user_email": ["u1@example.com"], "total": [100]})
    monkeypatch.setattr(agent_module, "_get_conn_duckdb", lambda: _StaticConn(user_df))
    profile = agent_module.perfil_usuario_360("u1")
    assert "total" in profile
    assert "u1@example.com" not in profile

    monkeypatch.setattr(agent_module, "_get_conn_duckdb", lambda: _StaticConn(pd.DataFrame()))
    assert "No se encontr" in agent_module.perfil_usuario_360("missing")


def test_resumen_ejecutivo_success_and_no_data(monkeypatch):
    class SummaryConn:
        def execute(self, sql):
            if "COUNT(*) FROM gold_user_360" in sql:
                return _FakeResult(row=(2,))
            if "COUNT(*) FROM gold_daily_metrics" in sql:
                return _FakeResult(row=(1,))
            return _FakeResult(
                pd.DataFrame(
                    {
                        "total_usuarios": [2],
                        "volumen_M_cop": [1.5],
                        "ticket_promedio": [750000],
                        "tasa_fallo_pct": [0.0],
                    }
                )
            )

    monkeypatch.setattr(agent_module, "_tablas_cargadas", {"gold_user_360": True, "gold_daily_metrics": True})
    monkeypatch.setattr(agent_module, "_get_conn_duckdb", lambda: SummaryConn())

    response = agent_module.resumen_ejecutivo()
    assert "KPIs GLOBALES" in response
    assert "total_usuarios" in response

    monkeypatch.setattr(agent_module, "_get_conn_duckdb",
                        lambda: _StaticConn(pd.DataFrame(), error=RuntimeError("sin datos")))
    assert "Error:" in agent_module.resumen_ejecutivo()


def test_ollama_model_build_inject_tool_call_and_stream(monkeypatch, requests_mock):
    model = agent_module.OllamaModel(model_id="llama3.2")

    chat = model._build_chat(
        [{"role": "user", "content": [{"text": "hola"}, {"text": "mundo"}]}],
        system_prompt="sistema",
    )
    assert chat == [
        {"role": "system", "content": "sistema"},
        {"role": "user", "content": "hola mundo"},
    ]
    assert model._inject_tools_in_system(chat.copy()) == chat

    def fake_tool(valor):
        return f"resultado {valor}"

    model._tools_registry = {"fake_tool": fake_tool}
    injected = model._inject_tools_in_system([{"role": "user", "content": "hola"}])
    assert injected[0]["role"] == "system"
    assert "fake_tool" in injected[0]["content"]

    monkeypatch.setattr(model, "_call_ollama", lambda chat: "interpretado")
    assert model._detect_and_call_tool('{"tool":"fake_tool","args":{"valor":7}}') == "interpretado"
    assert model._detect_and_call_tool('{"tool":"unknown","args":{}}') == '{"tool":"unknown","args":{}}'

    model._tools_registry = {"bad": lambda: (_ for _ in ()).throw(RuntimeError("fallo"))}
    assert model._detect_and_call_tool('{"tool":"bad","args":{}}') == '{"tool":"bad","args":{}}'

    monkeypatch.setattr(agent_module, "OLLAMA_BASE_URL", "http://ollama.local")
    requests_mock.post(
        "http://ollama.local/api/chat",
        json={"message": {"content": "respuesta"}},
    )
    assert agent_module.OllamaModel()._call_ollama([{"role": "user", "content": "hola"}]) == "respuesta"

    requests_mock.post("http://ollama.local/api/chat", exc=requests.ConnectionError)
    with pytest.raises(RuntimeError):
        agent_module.OllamaModel()._call_ollama([{"role": "user", "content": "hola"}])

    stream_model = agent_module.OllamaModel()
    monkeypatch.setattr(stream_model, "_call_ollama", lambda chat: "texto")
    monkeypatch.setattr(stream_model, "_detect_and_call_tool", lambda text: f"{text} final")

    async def collect():
        return [event async for event in stream_model.stream([{"role": "user", "content": "hola"}])]

    events = asyncio.run(collect())
    assert events[0]["messageStart"]["role"] == "assistant"
    assert events[2]["contentBlockDelta"]["delta"]["text"] == "texto final"
    assert stream_model.get_config()["model_id"] == "llama3.2"


def test_crear_agente_agent_query_and_reset(monkeypatch):
    monkeypatch.setattr(agent_module, "_verificar_ollama", lambda: False)
    with pytest.raises(RuntimeError):
        agent_module.crear_agente()

    class FakeAgent:
        def __init__(self, model, system_prompt, tools):
            self.model = model
            self.system_prompt = system_prompt
            self.tools = tools

        def __call__(self, pregunta):
            return SimpleNamespace(message={"content": [{"text": f"respuesta {pregunta}"}]})

    monkeypatch.setattr(agent_module, "_verificar_ollama", lambda: True)
    monkeypatch.setattr(agent_module, "_get_conn_duckdb", lambda: object())
    monkeypatch.setattr(agent_module, "Agent", FakeAgent)

    created = agent_module.crear_agente()
    assert isinstance(created, FakeAgent)
    assert "consultar_sql" in created.model._tools_registry

    monkeypatch.setattr(agent_module, "_respuesta_datos_confiable", lambda pregunta: "directa")
    assert agent_module.agent_query("kpi") == "directa"

    monkeypatch.setattr(agent_module, "_respuesta_datos_confiable", lambda pregunta: None)
    monkeypatch.setattr(agent_module, "_es_pregunta_de_seguimiento", lambda pregunta: False)
    agent_module._agent_instance = FakeAgent(None, "", [])
    assert agent_module.agent_query("hola") == "respuesta hola"
    agent_module._agent_instance = lambda pregunta: "texto plano"
    assert agent_module.agent_query("hola") == "texto plano"

    agent_module.reset_agent()
    assert agent_module._agent_instance is None
    assert agent_module._conversation_history == []
