# Guion de exposición (8–10 minutos)

## Preparación (el día anterior)

1. `python main.py check` debe dar OK con tu API key.
2. **Graba una corrida real:** `python main.py demo --scenario all`. Queda guardada en SQLite y aparece en la pestaña «Historial guardado» de la interfaz. Es tu plan B si la API falla en vivo: muestras decisiones reales ya registradas.
3. Ten abiertas dos cosas: la terminal con el proyecto y `streamlit run ui/app.py` en el navegador.
4. No uses el modo mock para la demo: no es IA y se nota (los motivos empiezan con «[MOCK sin IA]»).

## Cronograma

| Min | Tema | Qué decir | Qué mostrar |
|---|---|---|---|
| 0:00–1:00 | Problema | Queremos ver un agente de IA tomando decisiones en un entorno de riesgo. Usamos trading simulado porque es un entorno con datos, estado, riesgo y resultados medibles. Todo es dinero virtual y no pretendemos predecir el mercado. | Diapositiva o README: aviso de simulación |
| 1:00–2:30 | ¿Qué es un agente? | Percibe, razona, usa herramientas, actúa y observa el resultado, en ciclo. Un bot de reglas ejecuta `si RSI < 30 comprar`. Un agente decide qué consultar, pondera evidencias que se contradicen y explica por qué. | El ciclo: percibir, analizar, razonar, herramientas, decidir, actuar, observar |
| 2:30–4:00 | Arquitectura | Arquitectura híbrida: código para datos, indicadores, cartera, riesgo y ejecución; IA para interpretar, decidir y explicar. Así bajan los costos y todo lo crítico es verificable. | Diagrama de capas (README) |
| 4:00–5:00 | Herramientas | Seis herramientas. El modelo no sabe el precio hasta que llama a `get_market_data`. Su acción final es `execute_virtual_order`, que valida, verifica y ejecuta. | `agent/tools.py`, lista de herramientas |
| 5:00–6:00 | El mercado | Escenario «caída fuerte»: RSI extremadamente bajo pero con tendencia bajista. Es una señal contradictoria a propósito. | Interfaz: precio, indicadores, cartera 100 % en efectivo |
| 6:00–7:30 | Decisión en vivo | Pulsamos «Ejecutar 1 ciclo». Miren la traza: qué herramientas pidió, en qué orden, y cómo justifica la decisión citando cifras. | Decisión + motivo + traza expandida. En terminal: `python main.py demo --scenario dip` muestra los logs |
| 7:30–8:15 | Validación y ejecución | La decisión pasa por tres controles: formato, anclaje a herramientas y riesgo. Si el modelo se equivoca o inventa una cifra, se rechaza y se le devuelve el motivo. Se ejecuta una orden simulada y la cartera cambia. | Validación de riesgo (APPROVED/ADJUSTED), cartera antes y después |
| 8:15–9:15 | Historial y escenario final | Todo queda registrado. En el escenario «desplome», el stop-loss actúa por código aunque el agente no venda. Comparamos con «comprar y mantener» para no sobrevalorar. | Pestaña Historial, `python main.py history`, escenario crash |
| 9:15–10:00 | Cierre | Es un agente porque tiene estado, herramientas, razonamiento, acción y observación en ciclo. Limitaciones: datos sintéticos, simulación simple, un LLM no es determinista. No es una estrategia validada. | Diapositiva de limitaciones |

## Frases clave para memorizar

- «El modelo razona; el código verifica.»
- «Las cifras vienen de herramientas, no de la memoria del modelo.»
- «Si el LLM falla, el sistema hace HOLD: la opción segura es no operar.»
- «Es una simulación educativa, no una estrategia validada.»

## Si algo falla en vivo

| Problema | Qué hacer |
|---|---|
| La API no responde o hay límite de uso | Ve a «Historial guardado» y muestra la corrida grabada. Explica que el sistema ante fallos hace HOLD de seguridad (es parte del diseño). |
| El modelo tarda | Cuenta la arquitectura mientras espera; cada ciclo son 2 o más llamadas. |
| Decide algo distinto a lo esperado | Es esperable: un LLM no es determinista. Úsalo para explicar cómo se justificó y qué validaciones actuaron. |
| Sin internet | Todo funciona con datos locales; solo necesitas red para el LLM. |
