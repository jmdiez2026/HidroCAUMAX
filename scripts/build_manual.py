"""Build the offline user manual and its PDF-rendered QGIS preview pages."""
import hashlib
import json
from pathlib import Path
import shutil

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak
import pypdfium2

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'src/caumax_consulta/docs/manual'
SOURCES = ROOT/'docs/manual_sources'
TITLES = ['Presentación y primeros pasos', 'Instalación y barra de herramientas',
    'Capas de referencia y navegación', 'Método racional y configuración',
    'Tabla y gráfico de caudales', 'Generación de capas D8',
    'Exportaciones y capas mejoradas', 'Cuenca, informes y proyectos',
    'Actualizar, limpiar y consultar la ayuda', 'Resolución de incidencias']


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    candidates = [(Path('C:/Windows/Fonts/arial.ttf'), Path('C:/Windows/Fonts/arialbd.ttf')),
                  (Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'), Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))]
    regular, bold = next(pair for pair in candidates if all(p.is_file() for p in pair))
    pdfmetrics.registerFont(TTFont('Manual', str(regular)))
    pdfmetrics.registerFont(TTFont('ManualBold', str(bold)))
    pdfmetrics.registerFontFamily('Manual', normal='Manual', bold='ManualBold', italic='Manual', boldItalic='ManualBold')
    blue, navy = colors.HexColor('#007FA8'), colors.HexColor('#073D60')
    styles = {
        'title': ParagraphStyle('title',fontName='ManualBold',fontSize=27,leading=32,textColor=navy,spaceAfter=14),
        'h1': ParagraphStyle('h1',fontName='ManualBold',fontSize=20,leading=25,textColor=navy,spaceAfter=18),
        'h2': ParagraphStyle('h2',fontName='ManualBold',fontSize=12,leading=16,textColor=blue,spaceBefore=12,spaceAfter=7),
        'body': ParagraphStyle('body',fontName='Manual',fontSize=10.2,leading=15,spaceAfter=9),
        'small': ParagraphStyle('small',fontName='Manual',fontSize=8.5,leading=12,spaceAfter=8,textColor=colors.HexColor('#4D6171')),
    }
    story=[]
    def p(text, style='body'): story.append(Paragraph(text,styles[style]))
    def heading(number):
        if story: story.append(PageBreak())
        p(f'{number:02d} / {TITLES[number-1]}','h1')
    def picture(name, width=475):
        source=SOURCES/name
        if source.is_file():
            from PIL import Image as PILImage
            with PILImage.open(source) as im: ratio=im.height/im.width
            story.append(Image(str(source),width=width,height=width*ratio))
            story.append(Spacer(1,10))
    def table(rows, widths=(115,360)):
        data=[[Paragraph(cell,styles['small']) for cell in row] for row in rows]
        t=Table(data,colWidths=widths,hAlign='LEFT')
        t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#E4F2F7')),
            ('LINEBELOW',(0,0),(-1,-1),0.35,colors.HexColor('#D5E1E9')),('LEFTPADDING',(0,0),(-1,-1),8),
            ('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        story.append(t)

    story.append(Spacer(1,25))
    story.append(Image(str(ROOT/'src/caumax_consulta/assets/hidrocaumax.png'),width=126,height=126))
    story.append(Spacer(1,24))
    p('HidroCAUMAX v.1.0','title')
    p('Manual de usuario','h1')
    p('Edición para el paquete 1.0.11 · 26 de noviembre de 2026','small')
    p('Herramientas de cuencas, cartografía y caudales máximos para QGIS y CAUMAX 3.0. Este manual acompaña al complemento y se puede consultar sin conexión.')
    p('Primeros pasos','h2')
    for text in ['1. Instala y habilita CAUMAX y, después, HidroCAUMAX.',
                 '2. Carga las capas de CAUMAX y abre el Método racional.',
                 '3. Selecciona el punto de cálculo y calcula las variables intermedias.',
                 '4. Usa Calcular del recuadro GEA para consultar la tabla y el gráfico de caudales.',
                 '5. Abre Generar capas resolución mejorada si necesitas MDT, direcciones y red con mayor detalle.']:
        p(text)
    p('El nombre visible es HidroCAUMAX v.1.0. El número técnico 1.0.11 identifica esta entrega y permite ordenar las actualizaciones.','small')

    heading(2)
    p('Requisitos e instalación','h2')
    p('Esta entrega declara compatibilidad con QGIS 3.34 a 3.99, con Qt 5, y requiere CAUMAX 3.0. La comprobación de escritorio se realiza en QGIS 3.44.15. TauDEM y sus dependencias están incluidos para Windows de 64 bits.')
    p('Descarga el ZIP de la publicación y utiliza <b>Complementos → Administrar e instalar complementos → Instalar a partir de ZIP</b>. También se distribuye un instalador para Windows. Habilita el complemento para que la barra y las herramientas se activen automáticamente.')
    table([['Botón','Función'],['HidroCAUMAX v.1.0','Restaura las herramientas añadidas a CAUMAX.'],['Capas','Carga las referencias cartográficas.'],['Abrir cuenca','Abre un proyecto de cuenca guardado.'],['Limpiar capas','Retira los resultados generados del mapa; conserva los archivos.'],['Actualizar','Busca, descarga, verifica e instala una versión compatible.'],['Ayuda','Muestra este manual; Abrir PDF utiliza el lector del equipo.']])
    p('Carpeta de instalación','h2')
    p('En el perfil predeterminado de este equipo: <font size="8">C:\\Users\\JM_GIS\\AppData\\Roaming\\QGIS\\QGIS3\\profiles\\default\\python\\plugins\\caumax_consulta</font>. En otros equipos o perfiles cambia la parte correspondiente al usuario y al perfil.')
    p('Dentro del complemento, <b>ResolucionMejorada</b> contiene el P0 incluido y es el destino inicial de las exportaciones. El manual reside en <b>docs/manual</b>.')

    heading(3)
    p('Pulsa <b>Capas</b>. El grupo <b>HidroCAUMAX · Capas de referencia</b> se coloca inmediatamente encima de <b>CAUMAX_CAPAS</b>. El grupo general se abre; sus nuevas entradas aparecen apagadas y con la leyenda plegada.')
    table([['Entrada','Contenido'],['Estaciones AEMET','Inventario histórico de precipitación; no equivale a un catálogo completo de estaciones activas.'],['Extensión zonas inundables','Escenarios SNCZI T=10, 50, 100 y 500 años.'],['Peligrosidad SNCZI','Cartografía de peligrosidad de inundación.'],['Google Satellite','Fondo de imagen por teselas.'],['Tramos de ríos · Pfafstetter','Red hidrográfica clasificada.'],['Caracterización agroclimática','Variables cartográficas del servicio MAPA.'],['IGME','Mapa hidrogeológico a escala 1:1.000.000.'],['Corine Land Cover','Servicio de ocupación del suelo; la fuente cambia según la escala.'],['Po España (CLC2000)','Ráster local de 100 × 100 m, atribuido a AdrLBallesteros; última capa.']])
    p('Qué se descarga al navegar','h2')
    p('Marca la capa que quieras consultar. Los servicios WMS y XYZ solicitan las imágenes de la zona visible y del zoom actual. Las capas apagadas no solicitan imágenes del mapa. La preparación inicial puede consultar el catálogo del servicio.')
    p('AEMET, Pfafstetter y Corine están directamente dentro del grupo general. <b>Google Satellite</b> se coloca fuera, inmediatamente debajo de <b>Google Terrain</b>, inicialmente apagada. Repetir Capas incorpora las entradas que falten y conserva la visibilidad elegida.')
    p('Al abrir <b>Caracterización agroclimática</b> o <b>Peligrosidad de inundación · SNCZI</b> se ve la lista completa con las leyendas plegadas. Puedes desplegar después una capa individual. El P0 utiliza rojo → amarillo → verde, con 16 clases por cuantiles, interpolación lineal y etiquetas enteras.','small')
    p('La capa <b>Red &gt; 50 km²</b> se representa en rosa intenso para localizarla al seleccionar un punto. Se conserva su geometría, su grosor y el resto de capas.')
    p('Al cargar las capas originales de CAUMAX, se desactiva <b>Mapas raster del IGN</b> y se añade <b>Google Terrain</b> visible como fondo, fuera del grupo. Se mantiene el encuadre inicial de la península y Baleares. Repetir la carga reutiliza el fondo. Después puedes navegar normalmente; Terrain requiere conexión.')

    heading(4)
    p('Configurar capas de cálculo','h2')
    p('Al abrir este diálogo nativo se propone la última pareja compatible de MDT y direcciones exportada y registrada en <b>ResolucionMejorada</b>. Deben compartir malla y sistema de referencia. Si no existe una pareja válida, se mantienen las fuentes originales y se informa.')
    p('Se propone el P0 incluido, se conserva el <b>Factor de torrencialidad original de CAUMAX</b> y la cuenca mínima se establece inicialmente en <b>2 km²</b>. Los rásteres de precipitación no se sustituyen. <b>Aceptar</b> aplica la configuración; <b>Cancelar</b> conserva la anterior.')
    p('Cálculo en el Método racional','h2')
    p('Selecciona un río o un punto del mapa, indica el periodo de retorno y pulsa <b>Calcular variables intermedias</b>. Revisa los campos de CAUMAX. HidroCAUMAX muestra los decimales con coma y mantiene la representación numérica necesaria para el cálculo nativo.')
    picture('gea.png')
    p('Hay dos botones Calcular','h2')
    p('<b>Calcular original</b>, en la fila superior, obtiene el caudal individual del recuadro Resultado. No abre la tabla T/Q.')
    p('<b>Calcular del recuadro GEA</b> introduce primero el valor medio regional del coeficiente corrector de P0, obtenido de las mismas fuentes que Valores recomendados. Después calcula y abre la tabla y el gráfico. Si no puede obtener ese valor, informa del problema y no lo inventa.')
    p('La fila GEA reúne Calcular, Guardar cuenca…, Cuenca hidrográfica, Generar capas resolución mejorada y Limpiar capas. El botón original de CAUMAX conserva su función y su formato.')

    heading(5)
    p('La tabla incluye <b>T=2, 5, 10, 25, 100 y 500 años</b> y el periodo indicado por el usuario, sin duplicados. T se expresa en años y Q en m³/s. Los caudales se presentan centrados, con dos decimales y coma; el cálculo conserva su precisión interna.')
    picture('tabla.png',width=400)
    p('Selecciona una fila para resaltar el punto correspondiente; selecciona un punto para activar su fila. Usa <b>Etiqueta</b>, <b>Cuadrícula</b> y <b>Escala logarítmica T</b> para cambiar la presentación. La rueda amplía o reduce, el arrastre desplaza y el doble clic restablece la vista. Las marcas de los ejes son enteras.')
    p('Las consultas <b>Periodo → caudal</b> y <b>Caudal → periodo</b> son automáticas al editar la entrada. Sus resultados se incorporan a tabla y gráfico. Las consultas interpolan Q respecto a log(T) entre los valores disponibles.')
    p('<b>Permitir extrapolación</b> está desmarcado inicialmente y afecta a T→Q. Cuando se activa, prolonga la tendencia de los dos valores extremos válidos; la prolongación se representa discontinua. Q→T permanece dentro del intervalo disponible. Una consulta interpolada o extrapolada no cambia el resultado principal de CAUMAX.','small')

    heading(6)
    p('Abre <b>Generar capas resolución mejorada</b>. El diálogo independiente se adapta a su contenido y permite mantener visible el mapa de QGIS.')
    p('Genera las tres capas necesarias para ejecutar CAUMAX con resolución mayor de la original 500x500m (esquema D8-TauDEM/Esri): (1) ráster MDT, (2) ráster Direcciones de flujo, y (3) shape Red de Drenaje.','small')
    p('1. Selecciona la resolución disponible','h2')
    p('Las opciones de 25 × 25 m y 5 × 5 m necesitan cobertura de MDT. La referencia de 500 m permite volver al delineado original. Una malla de 5 m tiene 25 veces más celdas que una de 25 m para la misma superficie; el tiempo depende de la extensión y de los recursos disponibles.')
    p('2. Comprueba el punto de cierre','h2')
    p('El proceso conserva el punto original y busca un cierre compatible con la cuenca de referencia. <b>Ajustar punto de cierre</b> permite revisarlo con el mapa y los controles de desplazamiento. Comprueba la divisoria antes de exportar.')
    p('3. Genera los productos','h2')
    p('Pulsa <b>Generar capas D8</b>. El umbral de inicio de corriente regula la densidad de la red: un umbral menor genera más tramos. El modo Automático propone y ajusta un umbral a partir de la cuenca. El MDT se muestra cuando está preparado; la red y las direcciones se añaden al terminar.')
    p('4. Cancela si es necesario','h2')
    p('<b>Cancelar</b> solo aparece durante la generación. Detiene las tareas y evita publicar resultados tardíos. Espera a que el estado indique la cancelación y vuelve a generar con la resolución o el umbral elegidos.')
    p('Compatibilidad de direcciones','h2')
    p('El producto de direcciones para CAUMAX conserva el esquema <b>ESRI D8</b>: E=1, SE=2, S=4, SW=8, W=16, NW=32, N=64, NE=128. La codificación interna de TauDEM se convierte al exportar. No sustituyas este producto por un ráster TauDEM sin convertir.')

    heading(7)
    p('Exportar los tres productos','h2')
    p('Usa los botones <b>Exportar MDT (TIFF)</b>, <b>Exportar dirección de drenaje (TIFF)</b> y <b>Exportar Red de Drenaje (SHP)</b>. La carpeta propuesta es <b>ResolucionMejorada</b>; puedes elegir otro destino. Un Shapefile consta de varios archivos y debes conservarlos juntos.')
    p('Para que la selección automática de cálculo encuentre una pareja nueva, exporta el MDT y las direcciones de la misma generación en la carpeta predeterminada. Una exportación incompleta, una malla diferente o archivos cambiados después no se aceptan como una pareja válida.')
    p('Grupo HidroCAUMAX mejoradas','h2')
    p('Cada vez que abres Generar capas resolución mejorada, se cargan los conjuntos de datos GIS admitidos que haya en esa carpeta dentro del grupo <b>HidroCAUMAX mejoradas</b>, encima de CAUMAX_CAPAS. Se reutilizan los ya cargados, sin duplicarlos.')
    p('Inicialmente solo queda marcada <b>Red_drenaje_25m.shp</b>. Si ese archivo no existe, todas quedan apagadas. Las leyendas se presentan plegadas. El grupo de resultados de una generación D8 nueva conserva su presentación propia.')
    p('Relieve y datos originales','h2')
    p('La rampa de elevación y el sombreado ayudan a leer el relieve. Cambiar la simbología no modifica las cotas del MDT ni los valores de dirección de flujo.')
    p('Conservar exportaciones entre versiones','h2')
    p('Desde esta entrega se conserva una copia de las exportaciones fuera de la carpeta reemplazable del complemento, en <b>HidroCAUMAX/ResolucionMejorada</b> dentro del perfil QGIS. Al cargar el plugin se recuperan los archivos que falten sin sobrescribir los existentes.')
    p('Si actualizas por primera vez desde una versión que aún no hacía esa copia, conserva aparte tus exportaciones antes de sustituir su carpeta. Esta precaución es especialmente relevante al usar el instalador ZIP del gestor de QGIS.','small')

    heading(8)
    p('Cuenca hidrográfica','h2')
    p('Abre la cuenca desde el botón GEA o desde una consulta de capas de caudales máximos. La ventana reúne divisoria, curso principal, morfología, hipsometría y los resultados disponibles de P0 y precipitación. Si el caudal aún no se ha calculado o las entradas han cambiado, se indica que está pendiente.')
    p('La selección de resolución y el suavizado recalculan los resultados asociados a la superficie activa. <b>Original</b> y <b>Suavizada</b> controlan las divisorias visibles; <b>Grado</b> modifica el suavizado. <b>Exportar shape</b> guarda la geometría mostrada.')
    p('Consultas y gráficos','h2')
    p('Las tablas y curvas muestran los valores disponibles, sus unidades y la cobertura de los datos. Los valores ausentes se señalan como no disponibles. Revisa qué superficie y qué fuentes están activas antes de comparar dos resoluciones.')
    p('Guardar y recuperar','h2')
    p('<b>Guardar cuenca…</b> o <b>Guardar proyecto</b>, según la ventana, conserva la información de la cuenca para recuperarla con <b>Abrir cuenca</b> o <b>Abrir proyecto</b>. Un proyecto guardado restaura sus resultados y cierres; no equivale a ejecutar un cálculo nuevo con fuentes distintas.')
    p('<b>Informe (PDF)</b> prepara el informe de la cuenca con los resultados disponibles y el mapa. Antes de compartirlo, revisa punto de cierre, unidades, periodos, cobertura de datos y estado del cálculo.')
    p('Interpretación automática','h2')
    p('Las 19 filas de morfología se interpretan al terminar el cálculo, sin botón ni conexión a un servicio de IA. La tabla y el informe utilizan los mismos criterios. Al recalcular la cuenca, la resolución o la superficie suavizada, se actualizan también los comentarios; los datos no disponibles se identifican como tales.')
    p('Las clases de forma e hipsometría proceden del tema docente <b>Cuenca Hidrográfica</b>, revisión 21SEP, de la Universidad de Valladolid. Cada interpretación combina el valor, el intervalo aplicable y una lectura práctica. Mantén el puntero sobre la celda para consultar el texto completo y la referencia.','small')
    p('Se conserva la elongación calculada con Lb recta; la comparación docente que usa la longitud del cauce se identifica por separado como <b>Re(L)</b>. Los intervalos de Melton que el tema deja abiertos no se completan con categorías inventadas. El índice hipsométrico utiliza la media de cotas y el TRI se interpreta a la escala del MDT.','small')
    p('Criterios y límites: <link href="https://github.com/jmdiez2026/HidroCAUMAX/blob/main/docs/interpretation-criteria.md" color="#007FA8">documentación de la interpretación automática</link>.','small')
    p('Los mapas de referencia, el inventario AEMET y los resultados de cálculo tienen procedencias distintas. Consulta la descripción de cada capa para conocer la fuente y su fecha.','small')

    heading(9)
    p('Actualizar sin abrir el gestor','h2')
    p('Pulsa <b>Actualizar</b>. El complemento consulta la publicación estable compatible, descarga el manifiesto y el ZIP, verifica SHA-256, versión e inventario de archivos, e instala la actualización. QGIS vuelve a cargar las herramientas y comunica la versión que ha activado.')
    p('Las comprobaciones automáticas al abrir el plugin solo avisan: la instalación empieza cuando pulsas Actualizar. Si hay una generación pendiente, termina o cancela ese trabajo antes de actualizar. Si empiezas otro cálculo mientras se descarga, debes volver a pulsar Actualizar cuando termine.')
    p('Si la instalación o la activación falla, se intenta restaurar la versión anterior y se informa del resultado. Una descarga no se anuncia como actualización completada. Las copias de recuperación se conservan en <b>HidroCAUMAX/updates</b> dentro del perfil QGIS.')
    p('Dos alcances de limpieza','h2')
    table([['Dónde','Qué hace'],['Barra superior','Limpiar capas cancela trabajo pendiente y retira los resultados generados de QGIS. Sus archivos permanecen en disco.'],['Recuadro GEA o diálogo D8','El botón rojo Limpiar capas presenta una confirmación y el detalle de los archivos generados que se pueden borrar. Al aceptar, elimina esas capas y sus archivos identificados.']])
    p('La limpieza con borrado utiliza registros de propiedad y exportación; no debe borrar las fuentes originales, el P0 incluido ni capas ajenas por tener un nombre parecido. Cancelar la confirmación no borra nada.')
    p('Ayuda disponible sin conexión','h2')
    p('<b>Ayuda</b>, al extremo derecho de la barra, abre un diálogo con las páginas de este manual. El selector permite saltar a cada sección. <b>Abrir PDF</b> entrega el documento al lector PDF del equipo; si no hay lector asociado, el manual sigue disponible en el diálogo.')

    heading(10)
    table([['Situación','Comprobación y acción'],['No aparecen los botones GEA','Comprueba que HidroCAUMAX está habilitado. Pulsa su logo y vuelve a abrir el Método racional.'],['MDT 5 m sin productos','Lee el error concreto: cobertura, descarga, ventana, cierre o TauDEM. Comprueba que se ha descargado el MDT y que el cierre corresponde a la cuenca. Cancela y reintenta; 25 m puede servir para contrastar la zona.'],['La generación tarda','Comprueba el progreso y la extensión. Una malla más fina requiere más celdas. Puedes cancelar y cambiar la resolución o el umbral.'],['No aparece una red','Revisa el umbral de inicio de corriente y la cobertura. Activa Automático o reduce el umbral con criterio cartográfico.'],['Configurar capas no elige el par esperado','Exporta MDT y direcciones de la misma generación en ResolucionMejorada. Comprueba que ambos existan y no se hayan sustituido por otros archivos.'],['Referencia vacía','Activa la capa, comprueba la conexión y el zoom. El servidor puede no tener cobertura o estar temporalmente indisponible.'],['Actualizar falla','Lee el mensaje. Revisa conexión y permisos sobre la carpeta del plugin, finaliza cálculos y reintenta. No se debe interpretar una descarga incompleta como instalación correcta.'],['No se abre el lector PDF','Lee las páginas en Ayuda o asocia un lector PDF en el sistema operativo.']])
    p('Información útil para comunicar un fallo','h2')
    p('Indica versión técnica del plugin y QGIS, punto y CRS, resolución, paso ejecutado y mensaje completo. Para D8, añade la extensión, el umbral y si el error ocurrió en descarga, cálculo o exportación. El registro de mensajes de QGIS ayuda a conservar el error exacto.')
    p('Publicaciones y documentación','h2')
    p('<link href="https://github.com/jmdiez2026/HidroCAUMAX/releases" color="#007FA8">github.com/jmdiez2026/HidroCAUMAX/releases</link><br/><link href="https://github.com/jmdiez2026/HidroCAUMAX" color="#007FA8">Repositorio HidroCAUMAX y documentación ampliada</link>','small')

    def page(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(blue); canvas.setLineWidth(1); canvas.line(42,806,553,806)
        canvas.setFont('Manual',8); canvas.setFillColor(navy)
        canvas.drawString(42,818,'HidroCAUMAX v.1.0 | Manual de usuario')
        canvas.drawString(42,25,'Paquete 1.0.11 · Edición 26/11/2026')
        canvas.drawRightString(553,25,f'{doc.page} / {len(TITLES)}')
        canvas.restoreState()
    pdf=OUT/'Manual_HidroCAUMAX_v1.0.pdf'
    doc=SimpleDocTemplate(str(pdf),pagesize=A4,leftMargin=52,rightMargin=52,topMargin=54,bottomMargin=45,
        title='HidroCAUMAX v.1.0 · Manual de usuario',author='HidroCAUMAX',invariant=1)
    doc.build(story,onFirstPage=page,onLaterPages=page)
    rendered=pypdfium2.PdfDocument(str(pdf))
    if len(rendered)!=len(TITLES):
        raise ValueError(f'Unexpected page overflow: {len(rendered)} pages')
    for index in range(len(rendered)):
        rendered[index].render(scale=1.5).to_pil().save(OUT/f'page-{index+1:02d}.png',optimize=True)
    (OUT/'pages.json').write_text(json.dumps({'titles':TITLES,'pdf_sha256':hashlib.sha256(pdf.read_bytes()).hexdigest()},ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps({'pages':len(rendered),'pdf':str(pdf),'bytes':pdf.stat().st_size}))


if __name__=='__main__': build()
