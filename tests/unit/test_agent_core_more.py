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

    assert agent_module._normalizar_texto("  M\u00e9trica \u00c1gil  ") == "metrica agil"

    requests_mock.get(
        "http://ollama.local/api/tags",
        json={"models": [{"name": "llama3.2:latest"}]},
    )
    assert agent_module._verificar_ollama() is True

    requests_mock.get("http://ollama.local/api/tags", status_code=503)
    assert agent_module._verificar_ollama() is False

    requests_mock.get("http://ollama.local/api/tags", exc=requests.ConnectionError)
    assert agent_module._verificar_ollama() is False


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
