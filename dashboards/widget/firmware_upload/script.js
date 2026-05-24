self.onInit = function() {
    var $container = self.ctx.$container;
    var selectedFile = null;
    
    $container.find('#firmwareFile').on('change', function(e) {
        var file = e.target.files[0];
        var $info = $container.find('#fileInfo');
        
        if (file) {
            selectedFile = file;
            var sizeMB = (file.size / (1024 * 1024)).toFixed(2);
            $info.text(file.name + ' (' + sizeMB + ' MB)').show();
        } else {
            selectedFile = null;
            $info.hide();
        }
    });
    
    $container.find('#uploadBtn').on('click', function() {
        var file = selectedFile || $container.find('#firmwareFile')[0].files[0];
        var version = $container.find('#firmwareVersion').val();
        
        if (!file) {
            showStatus('Selecciona un archivo .bin primero.', 'error');
            return;
        }
        
        if (!version || parseInt(version) < 1) {
            showStatus('Introduce un numero de version valido (>= 1).', 'error');
            return;
        }
        
        var $btn = $container.find('#uploadBtn');
        $btn.prop('disabled', true).text('Subiendo...');
        
        var $progressBar = $container.find('#progressBar');
        var $progressFill = $container.find('#progressFill');
        $progressBar.show();
        $progressFill.css('width', '0%');
        
        showStatus('Subiendo firmware al servidor...', 'info');
        
        var formData = new FormData();
        formData.append('file', file);
        formData.append('version', parseInt(version));
        
        var xhr = new XMLHttpRequest();
        
        xhr.upload.addEventListener('progress', function(e) {
            if (e.lengthComputable) {
                var percent = Math.round((e.loaded / e.total) * 100);
                $progressFill.css('width', percent + '%');
            }
        });
        
        xhr.addEventListener('load', function() {
            if (xhr.status === 200) {
                $progressFill.css('width', '100%');
                var response = JSON.parse(xhr.responseText);
                showStatus('Firmware v' + response.version + ' subido (' + response.size + ' bytes). Enviando comando al ESP32...', 'success');
                
                sendRPC('updateFirmware', {}, function(ok, err) {
                    if (ok) {
                        showStatus('Comando enviado. El ESP32 descargara el firmware y se reiniciara.', 'success');
                    } else {
                        showStatus('Firmware subido pero fallo el comando: ' + err + '. Pulsa el boton OTA en el dispositivo.', 'error');
                    }
                    resetUI();
                });
            } else {
                var errorMsg = 'Error al subir: ' + xhr.statusText;
                try {
                    var resp = JSON.parse(xhr.responseText);
                    if (resp.detail) errorMsg = resp.detail;
                } catch(e) {}
                showStatus(errorMsg, 'error');
                resetUI();
            }
        });
        
        xhr.addEventListener('error', function() {
            showStatus('Error de red al subir el archivo.', 'error');
            resetUI();
        });
        
        xhr.open('POST', 'http://localhost:8000/api/firmware/upload');
        xhr.send(formData);
    });
    
    function sendRPC(method, params, callback) {
        self.ctx.controlApi.sendOneWayCommand(method, params, 5000)
            .subscribe(
                function() {
                    self.ctx.controlApi.completedCommand();
                    callback(true, null);
                },
                function(err) {
                    callback(false, err.message || err);
                }
            );
    }
    
    function resetUI() {
        var $btn = $container.find('#uploadBtn');
        $btn.prop('disabled', false).text('Subir y Actualizar');
        
        setTimeout(function() {
            $container.find('#progressBar').hide();
            $container.find('#progressFill').css('width', '0%');
        }, 3000);
    }
    
    function showStatus(msg, type) {
        var $el = $container.find('#status');
        $el.text(msg).removeClass('success error info').addClass(type).show();
    }
};
