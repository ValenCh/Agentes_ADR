# Documento técnico

## 1. Objetivo y alcance

Agente de IA experimental que, dentro de un entorno de trading **simulado**, analiza información de mercado de BTC/USDT y decide BUY, SELL o HOLD. No pretende predecir el mercado ni ser una estrategia validada. El foco es demostrar el concepto de agente: percepción, estado, objetivos, herramientas, razonamiento, acción y observación en un ciclo.

## 2. Arquitectura

```
                 +---------------------------+
   Datos ------> |  Herramientas (ToolBox)   |  get_market_data, calculate_indicators,
 (CSV/Binance)   +-------------+-------------+  get_portfolio, calculate_risk,
                               |                get_trade_history, execute_virtual_order
                               v
                 +---------------------------+
                 |  Agente (LLM + bucle)     |  razona, pide herramientas, decide
                 +-------------+-------------+
                               v
        Decisión (Pydantic) -> Control anti-alucinaciones -> Validación de riesgo
                               v
                 Broker simulado -> Cartera -> SQLite
```

Responsabilidades:

| Capa | Módulo | Tecnología | ¿IA? |
|---|---|---|---|
| Datos | `market/data_provider.py` | CSV, `requests` (solo GET público) | No |
| Indicadores | `market/indicators.py` | pandas | No |
| Cartera y ejecución | `simulation/portfolio.py`, `broker.py` | Python | No |
| Riesgo | `simulation/risk.py` | Python | No |
| Razonamiento y decisión | `agent/agent.py`, `llm.py`, `prompts.py` | LLM vía API compatible con OpenAI | **Sí** |
| Contrato de salida | `agent/schemas.py`, `grounding.py` | Pydantic | No |
| Control del ciclo | `simulation/runner.py` | Python | No |
| Historial | `storage/database.py` | SQLite | No |
| Interfaz | `ui/app.py`, `main.py` | Streamlit, argparse | No |

## 3. Ciclo del agente

`TradingSession.step()` (runner) rodea al agente:

1. Avanza la vela (o pide la última si es en vivo).
2. **Stop-loss automático (código):** si la posición cae más del 5 % respecto de su precio medio de entrada, vende sin consultar al modelo.
3. **Filtro de eventos (código):** decide si se despierta al agente (cruces de medias, RSI que entra en zona extrema, precio fuera de bandas, posición con resultado abierto de ±3 %, control periódico cada 24 ciclos). En modo `always` se consulta en cada ciclo.
4. `TradingAgent.run_cycle()`:
   - envía al LLM el prompt de sistema y un mensaje de ciclo **sin precios ni saldos**;
   - el LLM responde con llamadas a herramientas; el sistema las ejecuta y devuelve los resultados (observación);
   - se repite hasta que el agente llama a `execute_virtual_order` con una decisión aceptada, o se agotan los pasos (máx. 6 llamadas al LLM por ciclo).
5. Se guarda la fila del ciclo en SQLite (cartera antes y después, motivo, veredicto de riesgo, traza de herramientas).

Si el LLM falla, la API cae o no llega a una decisión válida, el sistema registra un **HOLD de seguridad**: nunca ejecuta algo por defecto.

## 4. Herramientas

| Herramienta | Devuelve | Origen del dato |
|---|---|---|
| `get_market_data` | precio, variación, últimas 6 velas | proveedor de datos |
| `calculate_indicators` | RSI14, EMA20, EMA50, SMA20, Bollinger, %B, distancias | `indicators.py` |
| `get_portfolio` | saldo, BTC, precio medio, PnL realizado y no realizado | `Portfolio` |
| `calculate_risk` | `max_buy_btc`, `max_sell_btc`, stop-loss, compras bloqueadas | `RiskManager` |
| `get_trade_history` | decisiones previas y cómo se movió el precio desde entonces | SQLite |
| `execute_virtual_order` | acción final: valida, verifica, aplica riesgo y ejecuta | `ToolBox` + broker |

**Indicadores elegidos** (solo los que aportan una dimensión distinta): RSI14 (momento), EMA20/EMA50 (tendencia) y Bandas de Bollinger 20,2 (volatilidad y ubicación del precio; la SMA20 es su banda media). Son evidencia, no reglas.

## 5. Decisión estructurada y validación en capas

```json
{"action": "BUY", "asset": "BTCUSDT", "amount": 0.02, "confidence": 0.7,
 "reason": "...", "cited_values": {"price": 58066.47, "rsi14": 5.17}}
```

Cada decisión atraviesa, en orden, estas barreras dentro de `execute_virtual_order`:

1. **Esquema (Pydantic):** acción válida, cantidad ≥ 0 y finita, confianza entre 0 y 1, motivo de longitud razonable, sin campos extra, HOLD con cantidad 0, BUY/SELL con cantidad > 0 y con cifras citadas.
2. **Consulta previa:** para operar debe haber consultado datos de mercado o indicadores, y la cartera.
3. **Anclaje a herramientas:** cada cifra citada debe coincidir (con tolerancia de redondeo) con lo que devolvió una herramienta en ese ciclo.
4. **Riesgo:** saldo, posición, tope por operación, máximo de BTC, orden mínima, pérdida máxima acumulada.
5. **Broker:** repite las comprobaciones de saldo y posición (defensa en profundidad).

Si una capa rechaza, el agente recibe el motivo y tiene hasta 2 intentos por ciclo. Agotados, el ciclo termina como rechazado (se registra la decisión, sin ejecutar).

## 6. Control de alucinaciones

- El mensaje del ciclo y el prompt no contienen precios ni saldos; solo llegan por herramientas (verificado por test).
- El precio que usa el sistema es siempre el del proveedor, nunca el que "diga" el modelo.
- `cited_values` se contrasta con los hechos que registraron las herramientas (`agent/grounding.py`, código determinista).
- Las cantidades se expresan en BTC y `calculate_risk` entrega los máximos, para que el modelo no tenga que calcular.
- Lo que el sistema **no** puede garantizar: que el razonamiento en texto sea correcto o esté bien fundamentado. Solo se verifican las cifras citadas y las acciones.

## 7. Gestión de riesgo

| Control | Valor por defecto | Efecto |
|---|---|---|
| Capital por operación | 25 % del valor total | recorta la cantidad (ADJUSTED) |
| BTC máximo en cartera | 0,10 | recorta o rechaza |
| Saldo suficiente (con comisión) | siempre | rechaza |
| Posición suficiente para vender | siempre | rechaza |
| Orden mínima | 10 USD | rechaza |
| Stop-loss | −5 % vs. precio medio | venta forzada por el sistema |
| Pérdida máxima acumulada | −20 % del capital inicial | bloquea nuevas compras |

Todos se configuran en `config.py` o `.env`.

## 8. Simulación

- Dinero virtual: 10 000 USD iniciales.
- Ejecución al **cierre de la vela actual** más comisión del 0,1 %. Sin deslizamiento ni liquidez.
- El costo de la posición incluye comisiones, de modo que ganancia realizada + no realizada ≈ valor total − capital inicial.
- `ReplayProvider` solo entrega velas hasta la actual: no hay información del futuro.
- Modos: escenario reproducible, backtest vela por vela y en vivo con datos públicos (con respaldo local).
- El resumen compara con "comprar y mantener" en el mismo tramo, para no sobrevalorar resultados.

## 9. Costos del modelo

Lo esperable son **2 llamadas por ciclo**: una con las herramientas de lectura en paralelo y otra con la decisión (así se comportó con el modelo guionado y el mock; con un LLM real puede variar). Una orden rechazada agrega una llamada. En backtest, el filtro de eventos evita consultar en los ciclos sin novedad (en una corrida de 300 velas con el mock se consultó 68 veces). Los ciclos de stop-loss no usan LLM.

## 10. Qué hace que sea un agente

| Característica | En este proyecto |
|---|---|
| Percepción | `get_market_data`, `calculate_indicators` |
| Estado | `Portfolio` + historial en SQLite |
| Objetivos | definidos en el prompt (valor de cartera con riesgo controlado) |
| Herramientas | 6 herramientas que el modelo elige cuándo llamar |
| Razonamiento | el LLM combina evidencia contradictoria y explica su decisión |
| Decisión | BUY / SELL / HOLD estructurado y validado |
| Acción | `execute_virtual_order` sobre el broker simulado |
| Observación | resultado de la orden dentro del ciclo y `get_trade_history` en el siguiente |
| Ciclo iterativo | `TradingSession.step()` en bucle |

Un bot de reglas tendría un `if` fijo sobre el RSI. Acá el modelo decide qué consultar, pondera señales que pueden contradecirse (p. ej. sobreventa en plena tendencia bajista) y produce una justificación.

## 11. Tecnologías

Python, pandas, Pydantic v2, requests, SDK `openai` (apuntando a cualquier endpoint compatible), SQLite, Streamlit y pytest. No se usan frameworks de agentes: el bucle es explícito en `agent/agent.py`.

## 12. Verificación y limitaciones

Se verificó con 107 pruebas (LLM guionado y mock, sin red). No se probó desde el entorno de desarrollo una llamada a un LLM real ni la API de Binance; el comando `python main.py check` está pensado para hacerlo. El modo mock no es IA. El dataset de ejemplo es sintético. Los resultados de la simulación no validan ninguna estrategia.

## 13. Evolución posible

Más activos, gestión de posiciones parciales, memoria de largo plazo, evaluación sistemática (varias corridas, distintos modelos) y comparación con un agente de aprendizaje por refuerzo.
