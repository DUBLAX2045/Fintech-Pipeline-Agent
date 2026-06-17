# Capa de Control Deterministico para Agente IA sobre Gold

Este documento define la arquitectura de control para el agente IA del proyecto Fintech Pipeline V3. El agente esta implementado en `src/agent/agent.py` y su interfaz en `src/agent/app.py`. El router deterministico vive en `src/agent/intent_router.py` y las reglas de seguridad SQL en `src/agent/security.py`.

La regla central es simple: **el codigo calcula y valida; el modelo redacta y explica**. El LLM (Ollama con llama3.2) no debe ser la fuente de verdad para maximos, minimos, fechas, filtros, formulas, periodos, rankings ni conclusiones criticas.

## 1. Resumen Del Objetivo

El objetivo es construir una capa intermedia entre el usuario y el modelo de lenguaje que:

- Interprete la pregunta del usuario.
- Valide si la pregunta se puede responder con la capa Gold.
- Detecte intencion, entidades, filtros, metricas y periodo.
- Ejecute calculos deterministas sobre datos reales.
- Genere hechos auditables antes de llamar al LLM.
- Obligue al LLM a redactar sin contradecir los hechos calculados.
- Mantenga contexto conversacional solo cuando sea correcto.
- Pida aclaracion cuando la pregunta sea ambigua.
- Corrija respuestas cuando el usuario detecte un error.
- Entregue respuestas utiles para usuarios financieros y no financieros.

## 2. Principios De Diseño Del Agente

| Principio | Regla practica |
|---|---|
| Fuente de verdad | La capa Gold y los calculos Python/SQL son la autoridad. |
| LLM controlado | Ollama redacta; no decide formulas criticas ni inventa columnas. |
| Evidencia visible | Toda respuesta importante debe mostrar datos, filtros o formula. |
| Ambiguedad honesta | Si falta entidad, metrica o periodo, se pide aclaracion. |
| Contexto con limites | Se conserva contexto si el usuario dice "eso", "esta grafica", "el anterior"; se reinicia si pide un tema nuevo. |
| Causalidad prudente | El agente puede decir "posible causa", no "causa definitiva", salvo que existan datos causales. |
| Trazabilidad | Cada respuesta debe poder reconstruirse: pregunta, intencion, tablas, columnas, filtros, SQL/calculo, resultado. |
| Dos audiencias | El mismo dato puede explicarse en modo profesional o modo claro. |
| Autocorreccion | Si el usuario contradice, el agente revisa los datos antes de defender la respuesta. |

## 3. Esquema Real De La Capa Gold (Fintech Pipeline V3)

Las tres tablas Gold disponibles son:

**`gold_user_360`** — una fila por usuario:
```
user_id, user_segment, city,
total_events, total_transactions, failed_transactions, failure_rate,
total_amount_cop, total_amount_usd, avg_ticket, balance_current,
top_merchant, top_category, preferred_channel, preferred_device,
last_transaction_date, last_event_date, days_since_last_tx
```

Segmentos disponibles: `premium`, `student`, `family`, `young_professional`  
Ciudades: Bogotá, Medellín, Cali, Barranquilla, Cartagena  
Merchants: Rappi, Éxito, Falabella, Nike, Netflix, Spotify, Amazon

**`gold_daily_metrics`** — una fila por día:
```
date, total_events, total_transactions, total_amount_cop, failed_count, unique_users
```

**`gold_event_summary`** — una fila por tipo de evento:
```
event, count, success_count, failed_count, pct_of_total
```

Tipos de evento: `USER_REGISTERED`, `MONEY_ADDED`, `PAYMENT_MADE`, `PURCHASE_MADE`, `TRANSFER_SENT`, `PAYMENT_FAILED`, `USER_PROFILE_UPDATED`

Columnas PII protegidas (filtradas por `src/agent/security.py`): `user_name`, `user_email`, `user_age`

El SYSTEM_PROMPT completo del agente esta definido en `src/agent/schema.py` junto con `GOLD_SCHEMA`. El router deterministico con 12 reglas de intencion esta en `src/agent/intent_router.py`. Las dimensiones graficables estan en `DIMENSIONES_GOLD` dentro de `src/agent/agent.py`.

---

Para el diseño general de control deterministico, el inventario de columnas sigue este formato:

```text
tabla
columna
tipo
descripcion
nullable
valores_ejemplo
minimo/maximo si numerica
minima_fecha/maxima_fecha si temporal
cardinalidad si categorica
regla_de_negocio
pii/sensible si aplica
```

### Tipos De Columnas

| Tipo | Uso principal | Riesgo de interpretacion | Regla de control |
|---|---|---|---|
| Identificador | usuario, cliente, orden, transaccion | Exponer PII o contar duplicados | No mostrar PII; usar conteos agregados. |
| Numerica | monto, saldo, ticket, cantidad, tasa | Confundir promedio por usuario vs por transaccion | Registrar formula exacta. |
| Categorica | segmento, canal, producto, estado | Categorias nulas o muy dispersas | Filtrar nulos y limitar top N. |
| Fecha | fecha evento, fecha compra, periodo | Mezclar periodos sin avisar | Siempre calcular rango usado. |
| Estado | exitoso, fallido, pendiente | Confundir estado operativo con financiero | Definir diccionario de estados. |
| Ubicacion | ciudad, pais, region | Geografia incompleta o sesgada | Advertir si hay nulos o baja cobertura. |
| Cliente/usuario | tipo cliente, antiguedad, actividad | Inferencias personales no soportadas | Responder por grupos, no por datos sensibles. |
| Producto/comercio | categoria, merchant, producto | Concentracion por top N | Mostrar universo y top N. |
| Calculada | failure_rate, churn, score, promedio | Formula no visible | Incluir formula o definicion. |

### Que Debe Detectar El Perfilador Gold

- Columnas disponibles y no disponibles.
- Fechas minima y maxima por tabla.
- Numero de registros por tabla.
- Nulos por columna.
- Duplicados por identificador.
- Cardinalidad de categoricas.
- Rangos y outliers de numericas.
- Columnas candidatas a metricas.
- Columnas candidatas a dimensiones.
- Columnas candidatas a filtros.
- Columnas sensibles o PII.
- Relaciones entre tablas.

### Matriz General De Uso

| Necesidad | Columnas candidatas | Validacion previa |
|---|---|---|
| Filtro temporal | fechas | Existe fecha, rango suficiente, zona horaria clara. |
| Agrupacion | categoricas | Cardinalidad razonable, nulos controlados. |
| Ranking | numericas + categorias | Formula definida, top N explicito. |
| Alerta | tasas, estados, fechas | Umbral definido y periodo visible. |
| Tendencia | fecha + metrica numerica | Minimo dos puntos temporales. |
| Comparacion | dos grupos/periodos | Grupos comparables y mismo criterio. |
| Diagnostico | varias metricas | No afirmar causalidad sin evidencia. |
| Recomendacion | metrica + impacto | Explicar dato base y limitacion. |

## 4. Tipos De Preguntas Que Debe Soportar

### Descriptivas

- "Como esta el negocio hoy?"
- "Dame un resumen de los datos."
- "Cuantos usuarios hay?"
- "Que esta pasando con las transacciones?"
- "Explicame esto en palabras simples."

### Comparativas

- "Compara los segmentos."
- "Que ciudad se comporta mejor?"
- "Quien tiene mayor ticket, web o app?"
- "Cual canal falla mas?"
- "Comparame este mes contra el anterior."

### Tendencia

- "Como ha cambiado el volumen en el tiempo?"
- "Hay una caida reciente?"
- "Muestrame la evolucion diaria."
- "Desde cuando viene bajando?"
- "Esto mejoro o empeoro?"

### Ranking

- "Top 10 usuarios por volumen."
- "Ciudades con mayor actividad."
- "Comercios mas importantes."
- "Productos con mas fallos."
- "Los peores canales por tasa de error."

### Diagnostico

- "Por que bajo el volumen?"
- "Que puede estar causando los fallos?"
- "Donde esta el problema?"
- "Que segmento necesita atencion?"
- "Hay algo raro en estos datos?"

### Alertas Y Anomalias

- "Hay alertas?"
- "Detecta comportamientos raros."
- "Que se sale de lo normal?"
- "Cuales metricas estan en rojo?"
- "Que debo revisar primero?"

### Seguimiento Conversacional

- "Y el mes anterior?"
- "Explicame ese valor."
- "Te equivocaste, revisa el minimo."
- "Esa grafica de donde sale?"
- "Y si lo miro por ciudad?"

### Auditoria

- "De donde sale ese numero?"
- "Que formula usaste?"
- "Que filtros aplicaste?"
- "Que tablas consultaste?"
- "Puedes justificar esa conclusion?"

### Ambiguas O Incompletas

- "Como vamos?"
- "Dame los mejores."
- "Que paso ayer?"
- "Lo ves bien?"
- "Quiero una grafica de eso."

### Deben Rechazarse O Aclararse

- "Predice exactamente el proximo mes" si no hay modelo predictivo.
- "Dime la causa real" si solo hay datos descriptivos.
- "Muestrame datos personales" si implica PII.
- "Cual cliente va a abandonar seguro" si no hay modelo validado.
- "Inventate una estrategia con datos externos" si debe usar solo Gold.

## 5. Mapa De Intenciones

| Intencion | Identificacion | Datos requeridos | Validaciones | Respuesta |
|---|---|---|---|---|
| Consultar valor | "cuanto", "total", "promedio" | metrica | columna existe, formula clara | valor + filtro + periodo |
| Resumen | "resumen", "como vamos" | KPIs base | tablas actualizadas | KPIs + lectura |
| Comparar | "vs", "comparar", "mejor/peor" | dimension + metrica | minimo 2 grupos | tabla + brechas |
| Max/min | "mayor", "menor", "alto", "bajo" | metrica ordenable | nulos controlados | maximo/minimo validados |
| Tendencia | "tiempo", "evolucion", "historico" | fecha + metrica | minimo 2 fechas | tendencia + periodo |
| Ranking | "top", "ranking" | dimension + metrica | top N, orden | lista ordenada |
| Diagnostico | "por que", "causa", "problema" | varias metricas | no afirmar causalidad | hipotesis + evidencia |
| Alerta | "alerta", "riesgo", "anomalia" | umbrales | umbral definido | severidad + accion |
| Grafica | "grafica", "muestra", "visualiza" | dimension/metrica | tipo grafico compatible | visual + interpretacion |
| Reporte | "informe", "reporte", "exporta" | resumen + detalle | formato disponible | link/ruta + resumen |
| Seguimiento | "eso", "esta grafica", "anterior" | contexto previo | referencia unica | respuesta contextual |
| Aclaracion | pregunta incompleta | intencion parcial | faltan datos | pregunta concreta |
| Fuera de datos | columna/tema inexistente | esquema Gold | no existe evidencia | rechazo transparente |

## 6. Reglas Deterministicas

### Reglas De Esquema

1. Si una columna no existe, no se consulta ni se inventa.
2. Si una metrica tiene alias, se mapea a una formula aprobada.
3. Si una columna es PII, no se muestra en respuestas ni tablas.
4. Si hay varias columnas candidatas, se pide aclaracion.

### Reglas De Periodo

1. Toda tendencia requiere columna fecha.
2. Si el usuario no especifica periodo, se usa el periodo Gold disponible y se declara.
3. Si pide "ayer", "hoy" o "ultimo mes", se convierte a fechas exactas antes de consultar.
4. Si la tabla no tiene fecha, se prohíbe hablar de evolucion temporal.

### Reglas De Comparacion

1. Comparar exige minimo dos grupos, entidades o periodos.
2. La formula debe ser identica para todos los grupos.
3. Si los grupos tienen tamaños muy diferentes, se prefiere tasa/promedio sobre total bruto.
4. Se calculan maximo, minimo, brecha absoluta y brecha relativa por codigo.

### Reglas De Grafica

1. Linea solo si hay columna fecha.
2. Barras para categorias comparables.
3. Torta solo para participacion y pocas categorias.
4. Si el usuario pide linea sin fecha, convertir a barras y explicarlo.
5. Toda grafica debe incluir interpretacion y lectura validada.

### Reglas De Diagnostico

1. "Causa" se responde como hipotesis si no hay experimento o variable causal.
2. Se listan evidencias a favor y limitaciones.
3. Si faltan variables clave, se dice explicitamente.
4. Recomendacion sin evidencia queda prohibida.

### Reglas De Seguimiento

1. "Esta grafica", "ese valor", "lo anterior", "te equivocaste" conservan contexto.
2. "Nuevo tema", "otra pregunta", "ahora analiza..." reinician contexto.
3. Si hay dos posibles referentes, se pide aclaracion.
4. Si el usuario corrige, se recalcula antes de responder.

## 7. Politica Anti-Alucinaciones

El agente tiene prohibido:

- Inventar columnas, fechas, usuarios, productos, ciudades o valores.
- Presentar hipotesis como certeza.
- Mezclar tablas o periodos sin avisar.
- Usar conocimiento externo para responder una pregunta que depende de Gold.
- Ocultar nulos, filtros, limites de filas o cambios de tipo de grafica.
- Recomendar acciones sin dato base.

### Frases Estandar

| Caso | Frase recomendada |
|---|---|
| No hay datos | "No encuentro registros suficientes en la capa Gold para responder con evidencia." |
| Pregunta ambigua | "Puedo responderlo, pero necesito precisar la metrica o el periodo." |
| Columna inexistente | "Esa variable no existe en el esquema Gold disponible; puedo analizar estas alternativas: ..." |
| Periodo faltante | "No indicaste periodo; usare el rango disponible en Gold: X a Y." |
| Calculo imposible | "Ese calculo requiere una columna que no esta disponible o no tiene valores suficientes." |
| Inconsistencia | "Detecte una inconsistencia entre la interpretacion y los datos; recalculo antes de concluir." |
| Correccion | "Tienes razon: al validar la tabla, el valor correcto es X. La respuesta anterior debio decir Y." |
| Hipotesis | "Con estos datos no puedo afirmar causalidad; la hipotesis mas soportada es..." |

## 8. Manejo De Errores Y Autocorreccion

Flujo:

1. Usuario contradice o corrige.
2. Clasificador marca la pregunta como seguimiento correctivo.
3. Se recupera el ultimo contexto estructurado.
4. Se recalculan metricas sobre los datos originales.
5. Se compara respuesta anterior vs hechos validados.
6. Si hubo error, se reconoce brevemente.
7. Se entrega respuesta corregida.
8. Se actualiza contexto.
9. Se registra trazabilidad.

Plantilla:

```text
Tienes razon, revise nuevamente los datos.

Correccion:
- Valor anterior indicado: X
- Valor correcto validado por codigo: Y
- Dato usado: tabla/columna/filtro

Interpretacion actualizada:
...
```

## 9. Reglas De Cohesion Conversacional

| Situacion | Accion |
|---|---|
| "y el mes anterior?" | Mantener metrica y entidad anterior; cambiar periodo. |
| "comparalo con el otro" | Resolver referentes; si hay varios, pedir aclaracion. |
| "esa grafica" | Usar ultimo contexto de grafica. |
| "te equivocaste" | Activar revision y autocorreccion. |
| cambia metrica | Mantener filtros si aplica, declarar cambio. |
| cambia entidad | Actualizar contexto principal. |
| tema nuevo explicito | Reiniciar contexto. |
| pregunta ambigua tras varios turnos | Resumir contexto y pedir precision. |

## 10. Formatos De Respuesta

### Respuesta Corta

```text
Respuesta: X.
Periodo: A a B.
Fuente: tabla/columna.
```

### Respuesta Con Calculo

```text
Resultado: X.
Formula: SUM(monto) / COUNT(usuario).
Filtros: periodo, segmento, estado.
Interpretacion: ...
Confianza: alta/media/baja segun cobertura.
```

### Comparacion

```text
Ganador: A con X.
Menor valor: B con Y.
Brecha: Z.
Lectura: ...
Limitacion: ...
```

### Ranking

```text
Top N por metrica:
1. ...
Datos usados: ...
Nota: se excluyeron nulos si aplica.
```

### Alerta

```text
Alerta: alta/media/baja.
Evidencia: metrica vs umbral.
Impacto probable: ...
Accion sugerida: ...
```

### Aclaracion

```text
Puedo responderlo, pero necesito una precision:
1. metrica
2. periodo
3. dimension
```

## 11. Escenarios De Prueba

| Pregunta | Intencion | Validacion | Respuesta esperada | Riesgo | Regla |
|---|---|---|---|---|---|
| "Dame el resumen ejecutivo" | resumen | KPIs existen | KPIs + interpretacion | bajo | resumen |
| "Que ciudad tiene menor ticket?" | min | ciudad + metrica | minimo validado por codigo | medio | max/min |
| "Grafico de lineas por ciudad" | grafica | no hay fecha | convertir a barras | alto | grafica compatible |
| "Por que bajo el volumen?" | diagnostico | periodo + metricas | hipotesis, no causalidad | alto | causalidad prudente |
| "Y el mes anterior?" | seguimiento | contexto previo | misma metrica, periodo anterior | medio | cohesion |
| "Te equivocaste, revisa el minimo" | correccion | contexto + recalculo | reconoce/corrige | alto | autocorreccion |
| "Cual cliente abandono seguro?" | prediccion | modelo inexistente | no afirmar certeza | alto | anti-alucinacion |
| "Muestrame datos personales" | PII | politica privacidad | rechazo | alto | PII |
| "Como vamos?" | ambigua | falta metrica | pedir o resumen default | medio | aclaracion |
| "Top comercios por revenue" | ranking | dimension + formula | top N | bajo | ranking |
| "Compara web y app en fallos" | comparacion | dos canales | tasas comparables | medio | comparacion |
| "Predice ventas exactas" | prediccion | modelo no existe | tendencia historica o aclaracion | alto | prediccion |
| "Que paso ayer?" | temporal | fecha relativa | fecha exacta y datos | medio | periodo |
| "Por que Cali esta mal?" | diagnostico ambiguo | falta metrica | pedir precision | alto | aclaracion |
| "Dame la causa real" | causalidad | evidencia causal ausente | hipotesis limitada | alto | causalidad |

## 12. Arquitectura Logica Recomendada

```text
Usuario
  -> Normalizador de texto
  -> Clasificador de intencion
  -> Detector de contexto conversacional
  -> Extractor de entidades/filtros/periodo
  -> Validador contra esquema Gold
  -> Planificador de consulta/calculo
  -> Ejecutor SQL/Pandas sobre Gold
  -> Validador de resultado
  -> Constructor de hechos deterministas
  -> LLM redactor con hechos obligatorios
  -> Revisor anti-alucinacion
  -> Respuesta final + trazabilidad
```

### Componentes

| Componente | Responsabilidad |
|---|---|
| Normalizador | Minusculas, acentos, aliases, fechas relativas. |
| Clasificador | Decide intencion: resumen, grafica, ranking, diagnostico, etc. |
| Context Manager | Sabe si conservar o reiniciar contexto. |
| Schema Guard | Valida columnas, PII, tipos y formulas permitidas. |
| Query Planner | Construye SQL/cálculo determinista. |
| Executor | Consulta Gold local/Databricks. |
| Result Validator | Calcula nulos, max/min, brechas, periodo, outliers. |
| Fact Builder | Produce hechos obligatorios para el LLM. |
| LLM Writer | Redacta en modo profesional o claro. |
| Hallucination Guard | Verifica que la respuesta no contradiga hechos. |
| Trace Logger | Guarda pregunta, SQL, filtros, hechos, respuesta, timestamp. |

## 13. Recomendaciones Finales

1. Crear un **diccionario formal de esquema Gold** versionado.
2. Separar definitivamente tres responsabilidades: router, calculador determinista y redactor LLM.
3. Guardar el ultimo contexto como objeto estructurado, no solo texto.
4. Para cada respuesta, generar siempre un bloque de **hechos validados por codigo**.
5. Implementar un revisor posterior que compare la respuesta LLM contra maximos, minimos y periodos calculados.
6. Crear pruebas unitarias por intencion, no solo por funcion.
7. Crear pruebas conversacionales de 2 a 5 turnos.
8. Registrar trazabilidad en logs o tabla: pregunta, intencion, columnas, SQL, filtros, resultado.
9. Mantener dos prompts: profesional financiero y explicacion clara.
10. Convertir esta especificacion en checklist de calidad antes de cada release del agente.

## Informacion Necesaria Para El Diseño Exacto

Para convertir esta plantilla en reglas 100% ajustadas a una capa Gold especifica, pega o genera:

```text
1. Lista de tablas Gold.
2. Lista de columnas por tabla.
3. Tipo de dato de cada columna.
4. Descripcion de negocio.
5. Formula de columnas derivadas.
6. Periodo minimo y maximo disponible.
7. Campos PII o sensibles.
8. Dimensiones principales.
9. Metricas oficiales.
10. Umbrales de alerta aceptados.
11. Preguntas prioritarias del negocio.
12. Respuestas que el agente nunca debe dar.
```

Con eso se puede construir un router deterministico completo, una suite de pruebas conversacionales y una politica anti-alucinacion ajustada a la realidad exacta de la capa Gold.

---

## Implementacion Actual En El Proyecto

| Componente de esta especificacion | Archivo de implementacion |
|---|---|
| Clasificador de intencion (12 reglas) | `src/agent/intent_router.py` |
| Schema Guard + filtro PII | `src/agent/security.py` |
| Executor SQL (DuckDB/Databricks) | `src/agent/agent.py` → `_get_conn_duckdb()`, `consultar_databricks()` |
| 11 herramientas (@tool) | `src/agent/tools.py` + `src/agent/agent.py` |
| SYSTEM_PROMPT y esquema Gold | `src/agent/schema.py` |
| Dimensiones graficables | `DIMENSIONES_GOLD` en `src/agent/agent.py` |
| Historial de conversacion (4 turnos) | `_resolver_seguimiento_con_ollama()` en `src/agent/agent.py` |
| Deteccion de alertas | `detectar_alertas()` — 7 verificaciones con umbrales definidos |
| Generacion de reporte HTML | `generar_reporte_html()` — HTML autocontenido con graficos en base64 |
| Tests conversacionales e intencion | `tests/unit/test_agent_routing.py`, `tests/unit/test_agent_core_more.py` |
| Tests de UI y smoke | `tests/ui/test_dashboard_app.py` |
