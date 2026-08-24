# Parámetros de arranque

## Cómo usar los parámetros de arranque

Los parámetros de arranque personalizan cómo inicia MiniOS. Separa los parámetros con espacios en la línea de comandos del kernel.

### Syslinux

- Pulsa `Esc` durante la secuencia de arranque de MiniOS para acceder al menú de arranque.
- Pulsa `Tab` para editar las opciones de arranque.
- Introduce los parámetros y pulsa `Enter` para iniciar.

### GRUB

- Pulsa `E` en el menú de GRUB.
- Edita los parámetros de arranque al final de la línea de comandos.
- Pulsa `F10` para iniciar con la nueva configuración.

## Parámetros de arranque

La columna Aplicación distingue los parámetros normalmente aceptados en cada arranque de los ajustes de cuenta destinados a la configuración inicial. Con persistencia, los componentes de live-config normalmente se ejecutan solo una vez; consulta [live-config](/configuration/live-config.md).

| Parámetro | Aplicación | Descripción | Ejemplo |
|---|---|---|---|
| `from` | Cada arranque | Carga los datos de MiniOS desde un directorio, dispositivo o ISO. Una ISO remota por **`http://` solamente** inicia el [arranque por red](/installation/Network-Boot.md) (httpfs2). | `from=/minios/`  
`from=/Downloads/minios.iso`  
`from=http://domain.com/minios.iso`  
`from=/dev/sr0/minios`  
`from=/dev/disk/by-label/MyFlash/minios`  
`from=askdisk`  
`from=askdisk/customdir` |
| `load` | Cada arranque | Carga solo los módulos `.sb` que coincidan con un nombre, lista, expresión regular o rango numérico soportado. También filtra los módulos copiados por `toram=trim`. | `load=00-core`  
`load=core,kernel,firmware`  
`load=00,01,02`  
`load=00-03` |
| `noload` | Cada arranque | Excluye los módulos `.sb` coincidentes, incluso de `toram=trim`. | `noload=05-xfce-apps`  
`noload=xfce-apps,firefox`  
`noload=05,06`  
`noload=04-06` |
| `bext` | Cada arranque | Establece la extensión del bundle. Valor predeterminado: `sb`. | `bext=mymod` |
| `timing` | Cada arranque | Activa la salida de tiempos de inicio. | `timing` |
| `union` | Cada arranque | Selecciona el sistema de archivos union. | `union=aufs`  
`union=overlayfs` |
| `ip` | Cada arranque | **Solo arranque por red (PXE).** Dirección estática para la obtención temprana. Formato: `<client-ip>:<server-ip>:<gateway-ip>:<netmask>[:<port>]` (puerto HTTP predeterminado **7529**). `ip=` no vacío fuerza la descarga de datos PXE y omite medios locales. No es la configuración de NetworkManager de la sesión. Consulta [Arranque por red](/installation/Network-Boot.md). | `ip=192.168.1.10:192.168.1.1:192.168.1.1:255.255.255.0` |
| `cache` | Cada arranque | Tamaño de caché httpfs en MB para arranque de red por ISO HTTP (`from=http://…`). Consulta [Arranque por red](/installation/Network-Boot.md). | `cache=512` |
| `rd.break` | Cada arranque | Abre una shell de depuración al final de la etapa initramfs. | `rd.break` |
| `perchdir` | Cada arranque | Selecciona una sesión de persistencia numerada o una acción: `resume`, `new` o `ask`. Un dispositivo/ruta o el formato `askdisk` selecciona otra ubicación de persistencia. Sin parámetro de persistencia, MiniOS inicia limpio. | `perchdir=1`  
`perchdir=resume`  
`perchdir=new`  
`perchdir=ask`  
`perchdir=/dev/sda1/changes`  
`perchdir=/dev/disk/by-label/MyFlash/changes`  
`perchdir=askdisk`  
`perchdir=askdisk/customdir` |
| `perchsize` | Cada arranque | Tamaño del contenedor para `dynfilefs`, `raw` y `luks`; no aplica a `native` ni `squashfs`. Acepta un número entero en MB o un sufijo `M`/`MB`, `G`/`GB` o `T`/`TB`; GB y TB se convierten a 1000 MB y 1.000.000 MB. El límite es 1.000.000 MB, además limitado por el espacio disponible tras `perchreserve`; archivos raw y LUKS se limitan a 4000 MB en FAT32. Los nuevos contenedores raw y LUKS por defecto son de 4000 MB. Los DynFileFS creados por initramfs usan por defecto la capacidad disponible redondeada a la baja a 1000 MB; el Gestor de Sesiones lo establece por defecto en 4000 MB. | `perchsize=4000`  
`perchsize=32GB`  
`perchsize=1TB` |
| `perchreserve` | Cada arranque | Espacio libre, en MiB, reservado en el dispositivo de persistencia. Los contenedores nuevos o en expansión no lo consumen y MiniOS avisa cuando el espacio libre llega a ese valor. Predeterminado: 256; máximo: 4096. | `perchreserve=512`  
`perchreserve=1024` |
| `perchmode` | Cada arranque | Modo de almacenamiento de persistencia.  
`native` (predeterminado): un directorio en un sistema de archivos POSIX escribible.  
`dynfilefs`: un contenedor expandible, incluso en FAT32, NTFS o exFAT.  
`raw`: una imagen ext4 de tamaño fijo.  
`luks`: un contenedor ext4 cifrado con LUKS2; la creación y el desbloqueo requieren consola y soporte de crypt en el initramfs.  
`squashfs`: una instantánea comprimida existente, desempaquetada para la sesión. El Gestor de Sesiones puede crear y guardar instantáneas SquashFS del sistema en ejecución; el initramfs puede reanudar pero no crearlas. | `perchmode=native`  
`perchmode=dynfilefs`  
`perchmode=raw`  
`perchmode=luks`  
`perchmode=squashfs` |
| `perch` | Cada arranque | Activa la persistencia y reanuda la última sesión. Equivale a `perchdir=resume`. | `perch` |
| `toram` | Cada arranque | Copia MiniOS en la RAM. Sin valor, usa `full`; `full` copia el directorio completo de MiniOS, mientras que `trim` copia el conjunto de módulos seleccionado por `load` y `noload`. Los cambios persistentes se incluyen si se solicita persistencia. | `toram`  
`toram=trim`  
`toram=full` |
| `text` | Cada arranque | Inicia en modo consola de texto. | `text` |
| `automount` | Cada arranque | Habilita el montaje automático de dispositivos de almacenamiento. | `automount` |
| `debug` | Cada arranque | Activa diagnósticos adicionales en el arranque. | `debug` |
| `nozram` | Cada arranque | Desactiva el swap zram. | `nozram` |
| `zramsize` | Cada arranque | Establece el tamaño del swap zram en MiB. Si se omite, MiniOS lo calcula según la RAM total. | `zramsize=512`  
`zramsize=2048` |
| `zramcomp` | Cada arranque | Selecciona `lzo`, `lzo-rle`, `lz4`, `lz4hc` o `zstd`; la disponibilidad depende del kernel en ejecución. Si se omite, se mantiene el valor predeterminado del kernel. | `zramcomp=lzo`  
`zramcomp=lz4` |
| `default-target` | Cada arranque | Establece el target predeterminado de systemd. | `default-target=multi-user`  
`default-target=rescue` |
| `enable-services` | Cada arranque | Activa los servicios systemd especificados al arrancar. | `enable-services=ssh,docker`  
`enable-services=ssh` |
| `disable-services` | Cada arranque | Desactiva los servicios systemd especificados al arrancar. | `disable-services=apache2`  
`disable-services=nginx` |
| `novirtres` | Cada arranque | Desactiva los cambios automáticos de resolución de pantalla en máquinas virtuales. El valor predeterminado de XFCE es 1280x800. | `novirtres` |
| `virtres` | Cada arranque | Establece la resolución de pantalla de XFCE en máquinas virtuales. | `virtres=1920x1080`  
`virtres=1024x768` |
| `components` | Cada arranque | Ejecuta solo los componentes de live-config listados, en orden. | `components=hostname,user-setup,sudo` |
| `nocomponents` | Cada arranque | Ejecuta todos los componentes de live-config excepto los listados. | `nocomponents=anacron,apport` |
| `hostname` | Cada arranque | Establece el nombre de host del sistema. | `hostname=minios` |
| `username` | Configuración inicial | Establece el nombre de usuario creado para el inicio de sesión automático. | `username=live` |
| `user-default-groups` | Configuración inicial | Establece los grupos predeterminados del usuario creado. | `user-default-groups=audio,cdrom,video` |
| `user-fullname` | Configuración inicial | Establece el nombre completo del usuario creado. | `user-fullname="MiniOS Live User"` |
| `root-password` | Configuración inicial | Establece la contraseña de root en texto plano. | `root-password=toor` |
| `root-password-crypted` | Configuración inicial | Establece la contraseña de root como hash crypt. | `root-password-crypted=$y$j9T$...` |
| `user-password` | Configuración inicial | Establece la contraseña del usuario en texto plano. | `user-password=live` |
| `user-password-crypted` | Configuración inicial | Establece la contraseña del usuario como hash crypt. | `user-password-crypted=$y$j9T$...` |
| `locales` | Cada arranque | Establece uno o más locales del sistema. | `locales=en_US.UTF-8` |
| `timezone` | Cada arranque | Establece la zona horaria del sistema. | `timezone=Europe/Berlin` |
| `keyboard-model` | Cada arranque | Establece el modelo de teclado. | `keyboard-model=pc105` |
| `keyboard-layouts` | Cada arranque | Establece los diseños de teclado separados por comas. | `keyboard-layouts=us,de` |
| `keyboard-variants` | Cada arranque | Establece las variantes de teclado separadas por comas correspondientes a los diseños. | `keyboard-variants=,dvorak` |
| `keyboard-options` | Cada arranque | Establece las opciones de teclado. | `keyboard-options=grp:alt_shift_toggle` |
| `noroot` | Configuración inicial | Impide que live-config otorgue privilegios de sudo y policykit. | `noroot` |
| `noautologin` | Cada arranque | Impide que live-config configure el inicio de sesión automático en consola y entorno gráfico; la configuración persistente existente no se elimina. | `noautologin` |
| `nottyautologin` | Cada arranque | Impide solo la configuración del inicio de sesión automático en consola; la configuración persistente existente no se elimina. | `nottyautologin` |
| `nox11autologin` | Cada arranque | Impide solo la configuración del inicio de sesión automático en entorno gráfico; la configuración persistente existente no se elimina. | `nox11autologin` |
| `xorg-driver` | Cada arranque | Selecciona un driver de Xorg en lugar de la autodetección. | `xorg-driver=nouveau` |
| `xorg-resolution` | Cada arranque | Establece la resolución de Xorg en lugar de la autodetección. | `xorg-resolution=1920x1080` |
| `module-mode` | Cada arranque | Con `merged`, integra los cambios de configuración en el sistema live en ejecución. | `module-mode=merged` |
| `hooks` | Cada arranque | Obtiene y ejecuta hooks desde el sistema de archivos, medio live o URLs soportadas por wget. | `hooks=filesystem`  
`hooks=http://example.com/script.sh` |

Separa los comandos con espacios. Consulta las páginas de referencia de `man bootparam` para otros parámetros del kernel comunes a todas las distribuciones Linux.

Para información detallada sobre los parámetros de live-config, consulta [live-config](/configuration/live-config.md).

Para cargar MiniOS por red (PXE y HTTP ISO), consulta [Arranque por red](/installation/Network-Boot.md).
