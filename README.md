# Gifteando

<p align="center">
<img width="1027" height="972" alt="logo" src="https://github.com/user-attachments/assets/493de94e-ead1-449e-9703-6dbe6ebf5503" />
</p>

Aplicación de escritorio para buscar GIFs animados gratuitos, verlos y guardarlos en el equipo.

Está hecha con Python y [Flet](https://flet.dev/). Busca primero en portales con licencia y atribución, y puede ampliar el catálogo con Giphy y KLIPY si configuras una clave.

## Qué hace

- Busca GIFs animados al dejar de escribir, o con el botón Buscar, y los muestra en una cuadrícula.
- Abre un GIF a mayor tamaño: copiar enlace, copiar el archivo al portapapeles, copiar la atribución, marcarlo favorito o guardarlo.
- Guarda favoritos y recientes en este equipo (fuera del repositorio).
- Filtro **Solo licencia libre** (CC0, dominio público, CC BY y CC BY-SA).
- Carga más resultados de la misma búsqueda.
- Permite elegir el portal: sin clave, todos, uno concreto, favoritos o recientes.
- Icono en la bandeja del sistema: mostrar, buscar, favoritos, recientes, claves, ocultar y salir.

## Portales

| Portal | Clave | Contenido |
| --- | --- | --- |
| [Openverse](https://openverse.org/) | No | Fuente principal para contenido reutilizable. Informa licencia y atribución |
| [GifCities](https://gifcities.org/) | No | Animaciones retro del archivo GeoCities, sin registro |
| [Wikimedia Commons](https://commons.wikimedia.org/) | No | Contenido libre por la API pública, distinto de los catálogos comerciales |
| [Giphy](https://giphy.com/) | Sí, gratuita | Memes, reacciones y GIF populares. La aplicación muestra «Powered by GIPHY» |
| [KLIPY](https://klipy.com/) | Sí, gratuita | Memes, reacciones y GIF populares |

La API de Tenor dejó de aceptar búsquedas nuevas, así que no está incluida.

Openverse, GifCities y Wikimedia Commons funcionan al abrir la aplicación. Giphy y KLIPY solo se consultan si guardas una clave. El portal **Sin clave** consulta los tres primeros, con Openverse al frente. **Todos** añade Giphy y KLIPY cuando hay clave.

Poder descargar un GIF no significa que se pueda reutilizar. Al abrirlo, Gifteando muestra, si el portal los da, la URL original, la página de procedencia, el autor y la licencia. Si no hay licencia, lo indica.

Cada portal se consulta en `gifteando/providers.py` y se normaliza al mismo dato de GIF. La ficha del resultado lee esos campos, así que un portal nuevo no necesita una ficha distinta.

## Requisitos

- Python 3.11 o posterior
- En Linux, [Zenity](https://help.gnome.org/users/zenity/stable/) para el diálogo de guardar archivos
- En Linux, soporte de bandeja (AppIndicator). En Ubuntu o Debian:

```bash
sudo apt install zenity gir1.2-ayatanaappindicator3-0.1
```

Si el menú de la bandeja no aparece, instala también el paquete de AppIndicator de tu escritorio.

## Instalación

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Uso

```bash
python3 main.py
```

<p align="center">
<img width="1099" height="799" alt="gifteando" src="https://github.com/user-attachments/assets/040d2227-3cc0-461c-9ed4-1495cf5a6a52" />
</p>

Escribe al menos dos letras y espera un momento: la búsqueda se lanza sola. También puedes pulsar **Buscar** o elegir **Archivo → Buscar**. El portal inicial es **Sin clave**: Openverse, GifCities y Wikimedia Commons.

Al abrir un GIF:
- **Copiar enlace** pega la URL en el chat o el documento.
- **Copiar GIF** deja el archivo en el portapapeles del escritorio (pegar en apps que acepten archivos).
- **Copiar atribución** prepara título, autor, licencia y enlace.
- La estrella lo guarda en **Favoritos**. Los GIFs abiertos pasan a **Recientes**.

Activa **Solo licencia libre** para ocultar resultados sin licencia clara o con restricciones NC/ND. Encaja sobre todo con Openverse y Wikimedia Commons.

Mientras llegan los resultados se muestra un indicador de carga. Si un portal no responde o supera su límite por hora, el aviso aparece en la barra de estado y los demás portales siguen mostrando lo que hayan encontrado.

Las miniaturas se guardan en `~/.cache/gifteando/`. Favoritos, recientes y el filtro libre viven en `~/.config/gifteando/library.json`, junto a las claves en `config.json`. Nada de eso forma parte del repositorio.

### Menú

- **Archivo:** Buscar, Favoritos, Recientes, Ocultar en la bandeja y Salir.

Al cerrar la ventana (la X), la app sigue en la bandeja. Para salir del todo: **Archivo → Salir** o **Salir** en el menú del icono.
- **Editar:** Claves API…
- **Ayuda:** Acerca de Gifteando.

### Claves API

Giphy y KLIPY regalan una clave de prueba con un límite de llamadas por hora.

- Giphy: <https://developers.giphy.com/dashboard/>
- KLIPY: <https://partner.klipy.com/>

Las claves se guardan solo en este equipo, en `~/.config/gifteando/config.json`, con permisos de lectura para tu usuario. No forman parte del repositorio.

## Estructura

```text
main.py                 Arranque de la ventana
gifteando/app.py        Interfaz
gifteando/providers.py  Búsqueda en cada portal
gifteando/thumbs.py     Previsualizaciones reducidas y caché en disco
gifteando/library.py    Favoritos, recientes y filtro de licencia libre
gifteando/tray.py       Icono y menú de la bandeja del sistema
gifteando/config.py     Lectura y guardado de las claves
gifteando/models.py     Datos comunes de un GIF
empaquetar.sh           Ejecutable de escritorio con flet pack
requirements.txt
```

La cuadrícula usa una copia pequeña del GIF, guardada en la caché local. El archivo original se descarga al abrirlo o al guardarlo.

## Ejecutable

Con [flet pack](https://flet.dev/docs/publish/using-pyinstaller) se genera un programa que no pide tener Python instalado. Hace falta PyInstaller:

```bash
pip install pyinstaller
./empaquetar.sh
```

El resultado queda en `dist/`. El mismo comando sirve en Linux, Windows y macOS; en Windows y macOS puedes lanzarlo a mano:

```bash
flet pack -n Gifteando -i gifteando/img/logo.png --product-name Gifteando --add-data "gifteando/img:gifteando/img" main.py
```

En Linux, guardar un GIF sigue usando Zenity, que no va dentro del ejecutable: hay que tenerlo instalado en el equipo. Un paquete Flatpak puede incluir esa dependencia; el ejecutable de `flet pack` no.

## Atribución

Si publicas resultados de Giphy, mantén la atribución «Powered by GIPHY». KLIPY pide que el campo de búsqueda use el texto «Search KLIPY» cuando ese es el único portal activo. Los GIF de Openverse conservan la atribución de su licencia.
