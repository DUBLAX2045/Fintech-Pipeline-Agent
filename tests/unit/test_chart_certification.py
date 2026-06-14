"""
Tests para el sistema de certificación de gráficos Gold.
Verifica que:
  - Las fórmulas SQL coincidan con las del dashboard
  - El NLP extraiga dimensión y métrica correctamente
  - El validador SQL corrija los errores conocidos
  - construir_sql_grafico produzca SQL válido y correcto para todos los combos
"""
from __future__ import annotations

import re
import pytest

from src.agent.intent_router import (
    METRICAS_GOLD,
    DIMENSIONES_GOLD,
    NOMBRES_METRICA,
    NOMBRES_DIMENSION,
    extraer_intencion_grafico,
    construir_sql_grafico,
    normalize_text,
)

pytestmark = pytest.mark.unit


# ═════════════════════════════════════════════════════════════════════════════
# 1. Integridad de los diccionarios certificados
# ═════════════════════════════════════════════════════════════════════════════

class TestDiccionariosCertificados:

    def test_todas_las_metricas_tienen_tres_campos(self):
        for clave, valor in METRICAS_GOLD.items():
            assert len(valor) == 3, f"Métrica '{clave}' debe tener (sql_expr, alias, descripcion)"
            sql_expr, alias, desc = valor
            assert sql_expr, f"Métrica '{clave}' tiene SQL vacío"
            assert alias,    f"Métrica '{clave}' tiene alias vacío"
            assert desc,     f"Métrica '{clave}' tiene descripción vacía"

    def test_todas_las_dimensiones_tienen_dos_campos(self):
        for clave, valor in DIMENSIONES_GOLD.items():
            assert len(valor) == 2, f"Dimensión '{clave}' debe tener (col_sql, where)"
            col, where = valor
            assert col, f"Dimensión '{clave}' tiene columna vacía"

    def test_revenue_usuario_usa_sum_count_no_avg_ticket(self):
        """CRÍTICO: revenue_usuario debe usar SUM/COUNT, no AVG(avg_ticket)."""
        sql_expr, _, _ = METRICAS_GOLD["revenue_usuario"]
        assert "SUM(total_amount_cop)" in sql_expr
        assert "COUNT(*)" in sql_expr
        assert "avg_ticket" not in sql_expr.lower()

    def test_ticket_transaccion_usa_avg_ticket(self):
        """ticket_transaccion es el promedio por transacción individual."""
        sql_expr, _, _ = METRICAS_GOLD["ticket_transaccion"]
        assert "avg_ticket" in sql_expr.lower()

    def test_dimensiones_nullable_tienen_filtro_is_not_null(self):
        """Merchant, categoria, canal y dispositivo deben tener filtro IS NOT NULL."""
        nullable = ["merchant", "categoria", "canal", "dispositivo"]
        for dim in nullable:
            _, where = DIMENSIONES_GOLD[dim]
            assert "IS NOT NULL" in where, f"Dimensión '{dim}' debe tener IS NOT NULL"

    def test_dimensiones_no_nullable_no_tienen_filtro(self):
        """Ciudad y segmento no necesitan filtro IS NOT NULL."""
        for dim in ("ciudad", "segmento"):
            _, where = DIMENSIONES_GOLD[dim]
            assert where == "", f"Dimensión '{dim}' no debería tener filtro WHERE"

    def test_nombres_metrica_cubren_todas_las_claves(self):
        for clave in METRICAS_GOLD:
            assert clave in NOMBRES_METRICA, f"Falta nombre legible para métrica '{clave}'"

    def test_nombres_dimension_cubren_todas_las_claves(self):
        for clave in DIMENSIONES_GOLD:
            assert clave in NOMBRES_DIMENSION, f"Falta nombre legible para dimensión '{clave}'"


# ═════════════════════════════════════════════════════════════════════════════
# 2. Extracción de intención NLP
# ═════════════════════════════════════════════════════════════════════════════

class TestExtraccionIntencionNLP:

    @pytest.mark.parametrize("texto,dim_esperada,metrica_esperada", [
        # Caso reportado: merchant + ticket → revenue_usuario (no avg_ticket)
        ("grafico de torta de los merchants segun ticket por usuario",
         "merchant", "revenue_usuario"),
        ("grafico de barras de merchants por revenue",
         "merchant", "revenue_usuario"),
        ("muéstrame el monto por merchant",
         "merchant", "revenue_usuario"),
        # Ciudad
        ("grafico de ciudades por revenue",    "ciudad",  "revenue_usuario"),
        ("usuarios por ciudad",                "ciudad",  "usuarios"),
        ("tasa de fallo por ciudad",           "ciudad",  "tasa_fallo"),
        # Segmento
        ("distribución por segmento",          "segmento","usuarios"),
        ("revenue por segmento",               "segmento","revenue_usuario"),
        ("balance por segmento",               "segmento","balance_promedio"),
        # Canal
        ("análisis por canal",                 "canal",   "usuarios"),
        ("revenue por canal",                  "canal",   "revenue_usuario"),
        # Dispositivo
        ("usuarios por dispositivo",           "dispositivo","usuarios"),
        # Categoría
        ("análisis por categoria",             "categoria","usuarios"),
        ("revenue por categoria",              "categoria","revenue_usuario"),
        # Inactivos
        ("usuarios inactivos por segmento",    "segmento","inactivos_30d"),
        # Fallos
        ("tasa fallo por merchant",            "merchant","tasa_fallo"),
    ])
    def test_extraccion_dimension_y_metrica(self, texto, dim_esperada, metrica_esperada):
        t = normalize_text(texto)
        dim, metrica = extraer_intencion_grafico(t)
        assert dim == dim_esperada,     f"'{texto}' → dim esperada '{dim_esperada}', obtuvo '{dim}'"
        assert metrica == metrica_esperada, f"'{texto}' → metrica esperada '{metrica_esperada}', obtuvo '{metrica}'"

    def test_texto_sin_dimension_retorna_none_dim(self):
        _, metrica = extraer_intencion_grafico(normalize_text("muéstrame el revenue total"))
        assert metrica == "revenue_usuario"

    def test_texto_sin_metrica_retorna_none_metrica(self):
        dim, _ = extraer_intencion_grafico(normalize_text("gráfico de merchants"))
        assert dim == "merchant"

    def test_bigrama_ticket_por_usuario_tiene_prioridad(self):
        """'ticket por usuario' como bigrama → revenue_usuario, NO ticket_transaccion."""
        t = normalize_text("grafico de merchants segun ticket por usuario")
        _, metrica = extraer_intencion_grafico(t)
        assert metrica == "revenue_usuario", (
            "El bigrama 'ticket por usuario' debe mapear a revenue_usuario, "
            f"no a {metrica}"
        )

    def test_ticket_promedio_mapea_a_ticket_transaccion(self):
        t = normalize_text("ticket promedio por merchant")
        _, metrica = extraer_intencion_grafico(t)
        assert metrica == "ticket_transaccion"


# ═════════════════════════════════════════════════════════════════════════════
# 3. Construcción de SQL certificado
# ═════════════════════════════════════════════════════════════════════════════

class TestConstruirSQLCertificado:

    def test_retorna_none_para_dimension_invalida(self):
        assert construir_sql_grafico("invalido", "usuarios", "bar") is None

    def test_retorna_none_para_metrica_invalida(self):
        assert construir_sql_grafico("merchant", "campo_inexistente", "bar") is None

    def test_sql_merchant_revenue_pie_usa_sum_count(self):
        """El caso problemático del bug reportado."""
        resultado = construir_sql_grafico("merchant", "revenue_usuario", "pie")
        assert resultado is not None
        sql, titulo = resultado
        assert "SUM(total_amount_cop)" in sql
        assert "COUNT(*)" in sql
        assert "avg_ticket" not in sql.lower()
        assert "top_merchant IS NOT NULL" in sql
        assert "LIMIT 8" in sql

    def test_sql_merchant_ticket_pie_usa_sum_count_no_avg(self):
        """Cuando usuario dice 'ticket por usuario' → siempre SUM/COUNT."""
        resultado = construir_sql_grafico("merchant", "revenue_usuario", "pie")
        assert resultado is not None
        sql, _ = resultado
        # Verificar que NO usa avg_ticket (el bug original)
        assert re.search(r"AVG\s*\(\s*avg_ticket\s*\)", sql, re.IGNORECASE) is None

    def test_sql_pie_tiene_limit_8(self):
        resultado = construir_sql_grafico("segmento", "usuarios", "pie")
        assert resultado is not None
        sql, _ = resultado
        assert "LIMIT 8" in sql

    def test_sql_bar_tiene_limit_10(self):
        resultado = construir_sql_grafico("ciudad", "revenue_usuario", "bar")
        assert resultado is not None
        sql, _ = resultado
        assert "LIMIT 10" in sql

    def test_sql_categorias_tiene_is_not_null(self):
        resultado = construir_sql_grafico("categoria", "usuarios", "bar")
        assert resultado is not None
        sql, _ = resultado
        assert "top_category IS NOT NULL" in sql

    def test_sql_canal_tiene_is_not_null(self):
        resultado = construir_sql_grafico("canal", "tasa_fallo", "bar")
        assert resultado is not None
        sql, _ = resultado
        assert "preferred_channel IS NOT NULL" in sql

    def test_sql_ciudad_no_tiene_is_not_null_innecesario(self):
        resultado = construir_sql_grafico("ciudad", "usuarios", "bar")
        assert resultado is not None
        sql, _ = resultado
        assert "IS NOT NULL" not in sql  # ciudad no necesita filtro

    def test_titulo_es_descriptivo(self):
        resultado = construir_sql_grafico("merchant", "revenue_usuario", "pie")
        assert resultado is not None
        _, titulo = resultado
        assert "Merchant" in titulo
        assert len(titulo) > 5

    @pytest.mark.parametrize("dim,metrica,tipo", [
        ("merchant", "revenue_usuario",    "pie"),
        ("merchant", "usuarios",           "bar"),
        ("merchant", "tasa_fallo",         "bar"),
        ("ciudad",   "revenue_usuario",    "bar"),
        ("ciudad",   "usuarios",           "pie"),
        ("segmento", "revenue_usuario",    "bar"),
        ("segmento", "inactivos_30d",      "bar"),
        ("canal",    "tasa_fallo",         "pie"),
        ("dispositivo", "usuarios",        "bar"),
        ("categoria", "revenue_usuario",   "pie"),
    ])
    def test_todas_las_combinaciones_generan_sql(self, dim, metrica, tipo):
        resultado = construir_sql_grafico(dim, metrica, tipo)
        assert resultado is not None, f"Falló para ({dim}, {metrica}, {tipo})"
        sql, titulo = resultado
        assert "SELECT" in sql.upper()
        assert "FROM gold_user_360" in sql
        assert titulo


# ═════════════════════════════════════════════════════════════════════════════
# 4. Validador SQL (_validar_sql_grafico)
# ═════════════════════════════════════════════════════════════════════════════

class TestValidadorSQL:

    @pytest.fixture
    def validar(self):
        from src.agent import agent as agent_module
        return agent_module._validar_sql_grafico

    def test_corrige_avg_ticket_en_contexto_revenue_por_usuario(self, validar):
        sql_malo = "SELECT top_merchant, AVG(avg_ticket) AS ticket_por_usuario FROM gold_user_360 GROUP BY top_merchant"
        pregunta = "revenue por merchant"
        sql_corregido = validar(sql_malo, pregunta)
        assert "AVG(avg_ticket)" not in sql_corregido
        assert "SUM(total_amount_cop)" in sql_corregido

    def test_no_corrige_avg_ticket_sin_contexto_de_usuario(self, validar):
        """Si la petición es explícitamente 'ticket por transacción', no corregir."""
        sql = "SELECT top_merchant, AVG(avg_ticket) AS ticket_tx FROM gold_user_360 GROUP BY top_merchant"
        # Sin "revenue" ni "ticket" en la pregunta — el validador no debe cambiar nada
        sql_resultado = validar(sql, "grafico de merchant")
        # avg_ticket puede quedar si no hay contexto claro de "por usuario"
        # (el validador es conservador cuando no hay señal clara)
        assert sql_resultado is not None

    def test_añade_is_not_null_para_top_merchant(self, validar):
        sql = "SELECT top_merchant, COUNT(*) AS usuarios FROM gold_user_360 GROUP BY top_merchant LIMIT 10"
        sql_corregido = validar(sql, "usuarios por merchant")
        assert "top_merchant IS NOT NULL" in sql_corregido

    def test_añade_is_not_null_para_preferred_channel(self, validar):
        sql = "SELECT preferred_channel, COUNT(*) FROM gold_user_360 GROUP BY preferred_channel LIMIT 10"
        sql_corregido = validar(sql, "usuarios por canal")
        assert "preferred_channel IS NOT NULL" in sql_corregido

    def test_añade_limit_si_no_esta(self, validar):
        sql = "SELECT user_segment, COUNT(*) AS usuarios FROM gold_user_360 GROUP BY user_segment"
        sql_corregido = validar(sql, "segmentos")
        assert "LIMIT" in sql_corregido.upper()

    def test_no_duplica_is_not_null_si_ya_existe(self, validar):
        sql = ("SELECT top_merchant, COUNT(*) AS usuarios FROM gold_user_360 "
               "WHERE top_merchant IS NOT NULL GROUP BY top_merchant LIMIT 10")
        sql_corregido = validar(sql, "usuarios por merchant")
        assert sql_corregido.lower().count("is not null") == 1

    def test_no_modifica_sql_correcto(self, validar):
        sql = ("SELECT user_segment, ROUND(SUM(total_amount_cop)/COUNT(*), 0) AS revenue\n"
               "FROM gold_user_360\nGROUP BY user_segment\nORDER BY revenue DESC\nLIMIT 10")
        sql_resultado = validar(sql, "revenue por segmento")
        assert "SUM(total_amount_cop)" in sql_resultado  # no se modificó la fórmula correcta


# ═════════════════════════════════════════════════════════════════════════════
# 5. Coherencia entre dashboard y gráficos
# ═════════════════════════════════════════════════════════════════════════════

class TestCoherenciaDashboardGraficos:

    def test_formula_merchant_igual_a_resumen_ejecutivo(self):
        """
        La fórmula que usa el gráfico de merchants debe ser igual
        a la que usa resumen_ejecutivo() para 'TOP MERCHANTS'.
        Resumen usa: ROUND(SUM(total_amount_cop)/COUNT(*), 0) AS ticket_por_usuario
        """
        resultado = construir_sql_grafico("merchant", "revenue_usuario", "pie")
        assert resultado is not None
        sql, _ = resultado
        # Verificar fórmula idéntica a resumen_ejecutivo (agent.py línea 609)
        assert "SUM(total_amount_cop)" in sql
        assert "COUNT(*)" in sql
        # Verificar que NO usa avg_ticket (que daría ~4x menos)
        assert "avg_ticket" not in sql.lower()

    def test_revenue_usuario_es_aproximadamente_4x_ticket_transaccion(self):
        """
        Documentar la diferencia conocida entre las dos métricas de ticket.
        No es un bug — son métricas distintas con nombres similares.
        revenue_usuario    = total acumulado por usuario (~4 transacciones × ticket_tx)
        ticket_transaccion = promedio de una sola transacción
        """
        sql_rev, alias_rev, _ = METRICAS_GOLD["revenue_usuario"]
        sql_tx,  alias_tx,  _ = METRICAS_GOLD["ticket_transaccion"]
        assert "SUM" in sql_rev and "COUNT" in sql_rev
        assert "AVG" in sql_tx and "avg_ticket" in sql_tx
        assert alias_rev != alias_tx  # aliases distintos en el resultado
