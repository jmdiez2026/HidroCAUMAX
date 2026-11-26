# Interpretación automática de la cuenca

HidroCAUMAX v.1.0, revisión técnica 1.0.11. La tabla de morfología y el informe PDF utilizan la misma función local para interpretar sus 19 parámetros. No se pulsa un botón ni se envían los resultados a un proveedor de IA. Los comentarios se actualizan al terminar el cálculo de la superficie activa; un dato ausente o inválido no se convierte en cero.

## Fuente docente y alcance

La referencia proporcionada por el usuario es **M1. Cuenca Hidrográfica, revisión 21SEP**, asignatura Hidrología 42173, Universidad de Valladolid. El documento identifica a Juan Manuel Diez-Hernández y Andrés Martínez de Azagra Paredes. Las páginas indicadas aquí corresponden a la numeración física del PDF de 68 páginas.

Este texto documenta los criterios numéricos utilizados y las decisiones de implementación. El PDF docente y sus diapositivas no se distribuyen con el complemento. Las clasificaciones se aplican sin redondear el valor; el formato visible usa coma decimal. Las convenciones en los extremos compartidos se explicitan a continuación.

## Forma: compacidad Kc y circularidad Ic

Se conserva la fórmula exacta `Kc = P / (2√(πA))`. El coeficiente 0,282 de la página 41 es una aproximación. La lectura combina la clase con el exceso porcentual del perímetro respecto a un círculo de igual superficie, `(Kc - 1) × 100`.

| Intervalo utilizado | Clase del tema |
|---|---|
| 1 ≤ Kc ≤ 1,25 | Compacta |
| 1,25 < Kc ≤ 1,50 | Transición |
| 1,50 < Kc ≤ 1,75 | Alargada y estrecha |
| Kc > 1,75 | Delgada, sinuosa o muy asimétrica |

Los extremos 1,25, 1,50 y 1,75 pertenecen a la clase inferior. Kc < 1 se señala como fuera del dominio. Las tendencias geométricas a concentrar o escalonar aportaciones se leen manteniendo iguales las demás condiciones. No sustituyen el cálculo de Tc ni de Q.

`Ic = 1/Kc²` expresa el mismo contraste de forma. Su interpretación indica el área como porcentaje del círculo de igual perímetro; no se cuenta como una segunda evidencia independiente. Se comprueba `0 < Ic ≤ 1`.

## Elongación: distinguir Re(Lb) y Re(L)

La página 40 utiliza el diámetro del círculo de igual área dividido por la **longitud total del cauce principal L**. La magnitud existente en HidroCAUMAX utiliza **Lb**, la mayor distancia recta desde el cierre a la divisoria. Se conserva esa magnitud calculada y no se le asignan directamente las clases de una fórmula distinta.

Cuando existen área y longitud de cauce válidas, el comentario deriva por separado `Re(L) = 2√(A/π) / L`, con A en km² y L en km, e identifica explícitamente esta comparación. El valor de la columna sigue siendo Re(Lb).

| Intervalo de Re(L) | Clase del tema |
|---|---|
| Re(L) < 0,50 | Muy alargada |
| 0,50 ≤ Re(L) < 0,70 | Alargada |
| 0,70 ≤ Re(L) < 0,80 | Moderadamente alargada |
| 0,80 ≤ Re(L) ≤ 0,90 | Subcircular |
| 0,90 < Re(L) ≤ 1 | Circular |

Los límites 0,50, 0,70 y 0,80 inician clase; 0,90 pertenece a subcircular. Para Re(L) > 1 se informa que queda fuera del intervalo docente considerado y se propone revisar recorrido y cierre. Si falta A o L, permanece la lectura cuantitativa de Re(Lb) sin inventar la comparación de cauce.

## Relieve relativo de Melton

Se conserva `MRN = (z máxima - z mínima) / √A`, con desnivel en metros y área en m². La tabla de la página 44 contiene únicamente los siguientes intervalos:

| Intervalo cubierto | Etiqueta de la tabla docente |
|---|---|
| MRN < 0,10 | Muy baja |
| 0,10 ≤ MRN ≤ 0,20 | Moderada |
| 0,30 ≤ MRN ≤ 0,60 | Alta |
| MRN > 4 | Torrencial extrema |

Los intervalos `0,20 < MRN < 0,30` y `0,60 < MRN ≤ 4` se presentan **sin clase en la tabla docente**. Se mantiene en todos los casos la lectura del desnivel respecto a √A y su contraste con pendiente y Tc.

La misma página rotula el ejemplo 0,409 como «muy fuerte», mientras su tabla lo coloca en «alta», y menciona otro umbral >3 en el texto. La implementación sigue los intervalos de la tabla y no crea una escala continua a partir de afirmaciones incompatibles. La etiqueta se identifica como clase docente; no es un diagnóstico de peligrosidad ni de transporte de sedimentos.

## Distribución hipsométrica

La página 54 proporciona las clases <0,35, 0,35-0,60 y >0,60. El programa conserva `HI = (z media - z mínima) / (z máxima - z mínima)`, utilizando **media**, no mediana P50. La mención «media (mediana)» del documento no altera esa definición.

| Valor | Lectura utilizada |
|---|---|
| 0 ≤ HI < 0,35 | Distribución baja; analogía docente «vieja» |
| 0,35 < HI < 0,60 | Distribución intermedia; analogía docente «madura» |
| 0,60 < HI ≤ 1 | Distribución alta; analogía docente «joven» |
| HI = 0,35 o HI = 0,60 | Límite entre clases docentes |

El comentario indica también la posición porcentual de la cota media dentro del desnivel total. Las analogías no son una datación ni una medida de erosión actual. Si el desnivel es cero, el índice no está definido; los valores fuera de [0,1] no se clasifican.

## TRI y pendiente

Las páginas 55-56 describen TRI de Riley y explican que la escala clásica no es universal respecto al tamaño de celda. La escala local 0-4, 4-7, 7-10 y >10 corresponde únicamente al ejemplo docente. No se traslada automáticamente a otras cuencas, aunque también empleen un MDT de 5 m.

Se conserva el promedio del TRI local de Riley, `√Σ(Δz²)`, sobre ocho vecinos. El comentario expresa su equivalente RMS medio `TRI / √8`, en metros, y la ventana física `3 × anchura` por `3 × altura` de celda. Esto permite comparar irregularidad con el mismo algoritmo y resolución, sin asignar una clase universal al promedio de toda la cuenca.

La pendiente media D8 se expresa como descenso local por cada 100 m horizontales. Las páginas 45-49 explican el cálculo, pero no proporcionan umbrales numéricos generales para clasificar esta media. Se conserva la pendiente D8; no se reemplaza con ella la pendiente longitudinal empleada en Tc.

## Otros parámetros y cálculo de caudales

El área se convierte en superficie aportante en hectáreas; la resolución, en superficie por celda; la mediana indica el percentil 50; las cotas extremas y el desnivel conservan su significado geométrico. Las longitudes y Tc describen el recorrido completo o el tramo central 10%-85%, según corresponda (páginas 36-39). La conversión a minutos facilita la lectura del tiempo utilizado para evaluar la intensidad.

No se cambia automáticamente al tramo central ni se modifican las fórmulas de CAUMAX, sus caudales, las mallas o la codificación ESRI D8. La interpretación no añade variables no calculadas: por ejemplo, no deduce la densidad de drenaje sumando una longitud de red que no esté disponible.

La procedencia y las condiciones de cada lectura están en la ayuda de la celda y en las definiciones del informe. La comprobación de aplicación del método racional sigue siendo distinta de la clasificación morfológica.
