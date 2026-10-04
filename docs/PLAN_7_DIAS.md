# Plan de trabajo de 7 días

El código base está completo y probado, así que el plan se centra en **montar, entender, probar con un LLM real y preparar la defensa**. Todo lo marcado como OPCIONAL puede omitirse sin romper el proyecto.

| Día | IMPRESCINDIBLE | OPCIONAL |
|---|---|---|
| **1** | Instalar Python y dependencias, crear el entorno, copiar `.env.example` a `.env`, obtener la API key, `pytest` verde, `python main.py check` OK | Leer el README completo |
| **2** | Entender datos e indicadores: `python main.py scenarios`, correr `demo --llm mock`, leer `market/` y `simulation/` (cartera, broker, riesgo) | Cambiar límites de riesgo en `.env` y ver qué cambia |
| **3** | Entender el agente: leer `agent/tools.py`, `prompts.py`, `agent.py`; correr `demo --scenario dip` con el LLM real; leer los logs | Ajustar el prompt y comparar decisiones |
| **4** | Probar los cuatro escenarios con el LLM real; revisar la traza y el historial (`python main.py history`); anotar decisiones interesantes | Correr un backtest corto (`backtest --steps 100`) |
| **5** | Interfaz: `streamlit run ui/app.py`; ensayar la demo completa; **grabar una corrida real de respaldo** (`demo --scenario all`) | Descargar datos reales (`scripts/download_history.py`) y hacer un backtest |
| **6** | Ensayo con cronómetro usando `docs/GUION_EXPOSICION.md`; preparar diapositivas con el diagrama; repasar las preguntas del profesor | Probar otro proveedor de LLM cambiando `.env` |
| **7** | Ensayo final, revisar README y documento técnico, verificar que todo corre desde cero en la computadora de la exposición | Modo en vivo (`python main.py live`) |

## Criterio de «listo» al final del día 5

- `pytest` sin fallos.
- `python main.py check` OK con el LLM real.
- Los cuatro escenarios corren y quedan guardados.
- La interfaz muestra precio, indicadores, saldo, BTC, valor de cartera, última decisión con su motivo, historial y ganancia/pérdida.

## Qué es opcional y por qué se puede quitar

- **Modo en vivo y descarga de datos reales** (`live`, `LiveProvider`, `download_history.py`): la demo funciona por completo con el dataset local.
- **Backtest largo**: la demo se apoya en los escenarios; el backtest es un extra.
- **Tests de interfaz** (`tests/test_ui_smoke.py`): el resto de la suite cubre la lógica.

## Riesgos de la semana

| Riesgo | Mitigación |
|---|---|
| La API gratuita tiene límites o cambia de modelo | Probar `main.py check` desde el día 1; tener una corrida grabada; cambiar `LLM_MODEL` en `.env` |
| El LLM decide algo distinto en la demo | Es normal: explicarlo como comportamiento de un modelo no determinista y mostrar el historial |
| Sin internet en la exposición | Solo el LLM necesita red; llevar la corrida grabada |
