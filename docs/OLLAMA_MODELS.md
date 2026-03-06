# Gestión de Modelos en Ollama

Este documento explica cómo se gestionan los modelos de lenguaje (LLMs) locales en el proyecto a través de Ollama, cómo funciona su persistencia y cuáles son los comandos útiles para administrarlos.

## Persistencia de los Modelos

En este proyecto, Ollama se ejecuta como un servicio dentro de un contenedor Docker (`wasap-ollama-1` o definido bajo el bloque `ollama` en `docker-compose.yml`).

Para evitar tener que descargar los modelos cada vez que se reinicia el contenedor (ya que los modelos de lenguaje suelen pesar varios Gigabytes), se utiliza un **Volumen de Docker** para hacer persistente la información.

En el archivo `docker-compose.yml`, la configuración de volumen para el servicio `ollama` se ve así:

```yaml
  ollama:
    image: ollama/ollama
    volumes:
      - ollama_data:/root/.ollama
```

Y en la sección principal de volúmenes:
```yaml
volumes:
  ollama_data:
```

### ¿Qué significa esto?
- El volumen nombrado `ollama_data` está vinculado al directorio `/root/.ollama` dentro del contenedor de Ollama.
- Todos los modelos descargados se guardan en `/root/.ollama/models`.
- Al estar en un volumen nombrado, **los datos persisten en tu disco duro (host)** independientemente de si apagas, reinicias o eliminas el contenedor de Ollama. Si creas o re-levantas el contenedor, Docker volverá a montar el volumen `ollama_data` y Ollama inmediatamente reconocerá los modelos previamente descargados.

---

## Comandos Útiles para Gestionar Modelos

Dado que Ollama se ejecuta en Docker, todos los comandos de CLI de Ollama deben ejecutarse **dentro** de ese contenedor usando `docker compose exec`. A continuación, se detallan los comandos más frecuentes, que debes ejecutar desde la raíz del proyecto (donde se encuentra tu `docker-compose.yml`).

### 1. Listar los modelos descargados
Muestra todos los modelos actualmente presentes en el almacenamiento persistente, junto con su peso (SIZE) y fecha de modificación.

```bash
docker compose exec ollama ollama list
```

### 2. Descargar un nuevo modelo
Descarga un modelo desde el registro central de Ollama (https://ollama.com/library) y lo guarda en el volumen `ollama_data`.

```bash
docker compose exec ollama ollama pull <nombre-del-modelo>
```
*Ejemplo para bajar Qwen 3.5 (9B de parámetros):*
```bash
docker compose exec ollama ollama pull qwen3.5:9b
```
*(Nota: El tiempo de descarga dependerá del tamaño del modelo y tu conexión a internet).*

### 3. Eliminar un modelo
Elimina un modelo del disco para liberar espacio.

```bash
docker compose exec ollama ollama rm <nombre-del-modelo>
```
*Ejemplo para borrar el modelo qwen3.5 de 9B:*
```bash
docker compose exec ollama ollama rm qwen3.5:9b
```

### 4. Actualizar un modelo existente
Si existe una versión más nueva de los pesos para un modelo (tag) que ya tienes descargado, puedes ejecutar el mismo comando `pull` para forzar a que Ollama descargue la actualización.

```bash
docker compose exec ollama ollama pull <nombre-del-modelo>
```

---

## Configurar el Asistente para usar un nuevo modelo

Después de descargar un nuevo modelo con `ollama pull`, debes instruir a WasAP para que comience a utilizarlo.

1. Abre tu archivo `.env`.
2. Busca la variable de entorno correspondiente que define el modelo de Ollama a usar (por lo general llamada `OLLAMA_MODEL` o similar asociada al chat principal).
3. Cambia su valor para que coincida exactamente con el nombre y tag del modelo bajado (ej. `qwen3.5:9b`).
4. Reinicia la aplicación base del asistente:

```bash
docker compose restart wasap
```
