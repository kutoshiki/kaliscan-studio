# KaliScan Studio

Aplicación local para planificar y continuar escaneos con herramientas de terminal en **Kali Linux**. Guarda la evidencia por proyecto, fecha, herramienta y ejecución. Los siguientes pasos se proponen a partir de resultados reales y los elige el usuario.

## Instalar desde el repositorio

En Kali Linux, clona este repositorio y entra en su carpeta:

```bash
git clone https://github.com/kutoshiki/kaliscan-studio.git
cd kaliscan-studio
```

Después:

```bash
sudo apt update
sudo apt install pipx
bash install.sh
kaliscan-studio
```

También puedes instalarlo directamente desde GitHub:

```bash
pipx install git+https://github.com/kutoshiki/kaliscan-studio.git
```

La instalación de la aplicación usa `pipx`; las herramientas de escaneo se gestionan con paquetes de Kali. Para que los botones de instalación y actualización funcionen desde la interfaz, Kali debe disponer de `pkexec` y un agente gráfico de autenticación. Si faltan, la pantalla muestra las herramientas necesarias para instalarlas desde una terminal.

## Primer arranque

1. En cada arranque la aplicación comprueba rutas y versiones de las herramientas. Puedes indicar una ruta manual cuando haya varias copias, actualizar el índice de Kali e instalar o actualizar paquetes desde la misma pantalla.
2. Crea un proyecto eligiendo nombre y carpeta. La pantalla inicial muestra los proyectos anteriores ordenados por su último escaneo.
3. Elige IP o web e introduce uno o varios objetivos y, si hace falta, exclusiones. Configura las pruebas y revisa el plan.
4. Tras la ejecución, abre **Resultados**, **Cambios**, **Siguientes pasos** e **Informe**. Puedes cerrar la aplicación y continuar el mismo proyecto después. Los planes interrumpidos se reanudan sin repetir tareas completadas.

La selección de formato JSON, CSV o HTML genera un resumen normalizado. Los archivos originales de las herramientas siempre se conservan. Los resultados vacíos se guardan como archivos vacíos cuando la herramienta no emitió hallazgos.

## Herramientas integradas

| Uso | Herramientas CLI |
| --- | --- |
| IP | Nmap y scripts NSE, incluido `--script vuln` |
| Web | Subfinder, httpx-toolkit, WhatWeb, ffuf, Nuclei, Nikto |
| TLS y SMB | testssl.sh, enum4linux-ng |

En Kali, el paquete y el ejecutable de ProjectDiscovery se llaman `httpx-toolkit`. Para ffuf selecciona una lista de palabras instalada en tu equipo.

El motor ejecuta una tarea a la vez y guarda su salida estándar, errores y formatos propios. Usa los argumentos de cada herramienta directamente, sin pasar texto a un shell. La duración máxima de cada tarea es de 30 minutos. Una tarea fallida se registra y permite conservar los resultados de las demás.

## Alcance de las comprobaciones

Configura solo objetivos para los que tienes autorización. La aplicación incluye la categoría completa NSE `vuln` porque forma parte del requisito de las prácticas. Nmap advierte que esta categoría puede contener scripts intrusivos o de explotación. Revisa **Ver scripts NSE seleccionados** en la vista previa antes de ejecutarla. La aplicación no incorpora Metasploit, explotación posterior ni acciones de red team.

Los hallazgos automáticos comienzan como **indicio**. En la pestaña Informe puedes registrar **comprobado** o **descartado**, añadir una nota y exportar HTML. La coincidencia de una versión con una vulnerabilidad conocida no equivale por sí sola a una confirmación.

Después de mapear puertos, la aplicación puede proponer detección de versiones y, con esos resultados, `--script vuln` o comprobaciones específicas para HTTP, TLS y SMB. Todas las propuestas posteriores necesitan que el usuario elija ejecutarlas.

## Estructura de un proyecto

```text
Mi-proyecto/
  project.json
  project.sqlite3
  2026-10-29/
    nmap/
      20261029-143200-abc123/
        192.0.2.1-xxxxxx.xml
        192.0.2.1-xxxxxx.txt
    resumen/
      20261029-143200-abc123/
        observaciones.json
  informes/
    informe-20261029-150000.html
```

La base SQLite sirve para navegar resultados y propuestas; los archivos originales quedan en el proyecto. Puedes abrir una carpeta de proyecto existente desde la pantalla de inicio.

## Desarrollo

Requiere Python 3.10 o posterior. Para ejecutar las pruebas del motor:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Para trabajar en la interfaz utiliza un entorno virtual e instala el paquete en modo editable con `pip install -e .` dentro de ese entorno. El proyecto está empaquetado con `pyproject.toml` y expone el comando `kaliscan-studio`.
