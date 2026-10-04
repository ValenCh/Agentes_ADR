# Guía paso a paso (Etapas A–L)

Cada etapa dice qué se construye, qué archivos la forman, cómo ejecutarla, cómo comprobar que funciona y qué errores son más probables. Los archivos ya vienen completos en el proyecto; la guía sirve para recorrerlos en orden, entenderlos y verificarlos.

Todos los comandos se ejecutan desde la carpeta `trading-agent` con el entorno virtual activado.

---

## Paso 0 — Requisitos e instalación

```bash
python --version      # 3.10 o superior (recomendado 3.11/3.12). En Windows puede ser: py --version
pip --version
git --version         # opcional
```

**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```
**macOS/Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

| Dependencia | Para qué sirve |
|---|---|
| `pandas` | velas e indicadores |
| `pydantic` | validar las decisiones del modelo |
| `requests` | pedir velas públicas (solo GET) |
| `python-dotenv` | leer `.env` |
| `openai` | cliente para cualquier API de chat compatible (Gemini, Groq, OpenRouter, Ollama, OpenAI) |
| `streamlit` | interfaz web |
| `pytest` | tests |

**Variables de entorno (`.env`):** `LLM_PROVIDER` (`api` o `mock`), `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_TEMPERATURE`. No hay ninguna variable de exchange, a propósito.

**Errores probables**
- *`python` no se reconoce (Windows):* usa `py -3.11 -m venv .venv`, o reinstala Python marcando «Add to PATH».
- *PowerShell no deja activar el entorno:* `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
- *`pip install` falla por versión de Python:* usa 3.10 o superior.

---

## Etapa A — Configuración del proyecto

**Qué se construye:** ajustes centrales (capital, límites de riesgo, modelo).
**Archivos:** `config.py`, `requirements.txt`, `.env.example`, `.gitignore`, `pytest.ini`.
**Comprobar:** `python main.py check` (mostrará la configuración; el paso del modelo fallará hasta que pongas tu clave, y eso es correcto).
**Errores probables:** `ModuleNotFoundError` → entorno virtual sin activar o dependencias sin instalar.

## Etapa B — Obtención de datos

**Decisión:** la fuente principal es un **CSV local** (reproducible, sin red, sin límites). Binance público es opcional y cae al CSV si falla. Descartamos `yfinance`: es scraping no oficial y se rompe con frecuencia.
**Archivos:** `market/data_provider.py`, `scripts/make_sample_data.py`, `scripts/download_history.py`, `data/`.
**Comprobar:**
```bash
python scripts/make_sample_data.py     # regenera el dataset sintético y los escenarios
pytest tests/test_data_storage_llm.py
```
**Punto clave para explicar:** `ReplayProvider` nunca entrega velas futuras; y el módulo de datos solo permite `GET` a `/api/v3/klines`.
**Errores probables:** `No existe el dataset` → ejecuta `make_sample_data.py`. Con `download_history.py`, un error de red significa que tu conexión bloquea Binance: sigue con el CSV de ejemplo.

## Etapa C — Indicadores

**Qué se construye:** RSI14 (momento), EMA20/EMA50 (tendencia), Bollinger 20,2 (volatilidad) con pandas, sin librerías de análisis técnico.
**Archivos:** `market/indicators.py`.
**Comprobar:** `pytest tests/test_indicators.py`.
**Errores probables:** «Se necesitan al menos 60 velas» → el dataset o la ventana es demasiado corto.

## Etapa D — Simulación de cartera, broker y riesgo

**Archivos:** `simulation/portfolio.py`, `simulation/broker.py`, `simulation/risk.py`.
**Comprobar:** `pytest tests/test_portfolio_broker.py tests/test_risk.py`.
**Punto clave:** el broker es la única pieza que modifica la cartera; el `RiskManager` es determinista.
**Errores probables:** ninguno esperable; si cambias límites en `.env`, algunos tests que asumen los valores por defecto pueden variar (los tests usan `Settings()` sin `.env`).

## Etapa E — Herramientas del agente

**Archivos:** `agent/tools.py`.
**Qué ver:** cada método del `ToolBox` es una herramienta; el modelo solo conoce los datos cuando la llama.
**Comprobar:** `pytest tests/test_agent_loop.py`.

## Etapa F — Razonamiento del agente

**Archivos:** `agent/prompts.py` (prompt de sistema), `agent/llm.py` (cliente), `agent/agent.py` (bucle).
**Comprobar sin IA:** `python main.py demo --scenario dip --llm mock` (muestra los logs del ciclo completo).
**Comprobar con IA real:**
```bash
python main.py check
python main.py demo --scenario dip
```
`check` hace una llamada mínima y verifica que el modelo **soporta herramientas**.
**Errores probables**
- *`Falta LLM_API_KEY`:* completa `.env`.
- *401/403:* clave incorrecta o sin permisos.
- *404 / modelo no encontrado o «no longer available»:* el proveedor retiró o renombró el modelo. Ejecuta `python main.py models` para ver los que ve tu clave, copia uno en `LLM_MODEL` y repite `python main.py check`.
- *429:* límite de uso de la capa gratuita; espera o baja `--steps`.
- *«Un agente necesita un modelo con function calling»:* elige otro modelo.
- *Error con `temperature`:* deja `LLM_TEMPERATURE=` vacío.

## Etapa G — Validación de decisiones

**Archivos:** `agent/schemas.py` (Pydantic), `agent/grounding.py` (control de alucinaciones).
**Comprobar:** `pytest tests/test_schemas_grounding.py`.
**Punto clave:** una decisión inválida o con cifras inventadas se rechaza y el agente recibe el motivo.

## Etapa H — Ejecución de operaciones simuladas

**Dónde ocurre:** `execute_virtual_order` en `agent/tools.py` encadena esquema → consulta previa → anclaje → riesgo → broker.
**Comprobar:** `pytest tests/test_agent_loop.py tests/test_runner.py`.

## Etapa I — Historial y runner

**Archivos:** `storage/database.py`, `simulation/runner.py`, `simulation/scenarios.py`, `main.py`.
**Comprobar:**
```bash
python main.py demo --scenario crash --llm mock
python main.py history
python main.py history --all
python main.py runs
```
**Nota:** en `crash` el stop-loss solo se activa si el agente no vende antes; con el mock puede vender por su propia regla. El test `test_stop_loss_sells_automatically_even_if_the_agent_only_holds` lo demuestra.
**Errores probables:** `database is locked` → hay otra ventana escribiendo; ciérrala.

## Etapa J — Interfaz

```bash
streamlit run ui/app.py
```
Muestra precio, indicadores, saldo, BTC, valor de cartera, ganancia/pérdida, última decisión con su motivo, traza de herramientas, historial y curva de valor de la cartera.
**Errores probables:** `ModuleNotFoundError: agent` → ejecuta el comando desde la raíz del proyecto; si el puerto está ocupado, `--server.port 8502`.

## Etapa K — Tests

```bash
pytest
```
107 pruebas: compra válida y sin saldo, venta válida y sin posición, límites de riesgo, decisión inválida del agente, actualización de cartera, cálculo de indicadores, ejecución simulada, control de alucinaciones, seguridad e interfaz.

## Etapa L — Demo final

Sigue `docs/GUION_EXPOSICION.md`. Antes de exponer, **graba una corrida real** con `python main.py demo --scenario all`: queda en SQLite y es tu respaldo si la API falla.

---

## Qué características hacen que sea un agente (para señalar durante la exposición)

| Etapa | Característica de agente |
|---|---|
| B–C | **Percepción** del entorno |
| D | **Estado** (cartera) |
| F | **Objetivos** (prompt) y **razonamiento** |
| E | **Herramientas** elegidas por el modelo |
| G–H | **Decisión** y **acción** sobre el entorno |
| I | **Observación** de resultados (historial) |
| Runner | **Ciclo iterativo** |
