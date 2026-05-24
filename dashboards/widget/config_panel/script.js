self.onInit = function() {
    var $container = self.ctx.$container;
    var selectedModelFile = null;
    
    loadModelInfo();
    
    $container.find('#btn-save').on('click', function() {
        var config = {};
        var ssid = $container.find('#wifi_ssid').val().trim();
        if (ssid) config.wifi_ssid = ssid;
        var pass = $container.find('#wifi_pass').val();
        if (pass) config.wifi_pass = pass;
        var broker = $container.find('#mqtt_broker').val().trim();
        if (broker) config.mqtt_broker = broker;
        var cmdTopic = $container.find('#mqtt_topic_cmd').val().trim();
        if (cmdTopic && cmdTopic !== 'waterly/comandos') config.mqtt_topic_cmd = cmdTopic;
        var datTopic = $container.find('#mqtt_topic_dat').val().trim();
        if (datTopic && datTopic !== 'waterly/datos') config.mqtt_topic_dat = datTopic;
        var gain = $container.find('#sensor_gain').val();
        if (gain !== '') config.sensor_gain = parseInt(gain);
        var integration = $container.find('#sensor_integration').val();
        if (integration) config.sensor_integration = parseInt(integration);
        var led = $container.find('#sensor_led_current').val();
        if (led !== '') config.sensor_led_current = parseInt(led);
        var pop = $container.find('#ble_pop').val().trim();
        if (pop) config.ble_pop = pop;
        
        if (Object.keys(config).length === 0) {
            showStatus('Rellena al menos un campo', 'error');
            return;
        }
        showStatus('Enviando configuracion al ESP32...', 'success');
        sendRPC('saveConfig', config);
    });
    
    $container.find('#btn-reset').on('click', function() {
        if (!confirm('¿Estas seguro? Se borraran TODAS las configuraciones.')) return;
        showStatus('Enviando Factory Reset...', 'error');
        sendRPC('factoryReset', {});
    });
    
    $container.find('#btn-download').on('click', function() {
        var xhr = new XMLHttpRequest();
        xhr.open('GET', 'http://localhost:8000/api/model/download');
        xhr.responseType = 'blob';
        
        xhr.addEventListener('load', function() {
            if (xhr.status === 200) {
                var blob = new Blob([xhr.response], { type: 'application/octet-stream' });
                var url = window.URL.createObjectURL(blob);
                var a = document.createElement('a');
                a.href = url;
                a.download = 'waterly_model.pkl';
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);
                showStatus('Modelo descargado correctamente.', 'success');
            } else {
                showStatus('No hay modelo para descargar. Entrena un modelo primero.', 'error');
            }
        });
        
        xhr.addEventListener('error', function() {
            showStatus('Error de red al descargar el modelo.', 'error');
        });
        
        xhr.send();
    });
    
    $container.find('#btn-upload-model').on('click', function() {
        var $field = $container.find('#modelUploadField');
        if ($field.is(':visible')) {
            $field.hide();
        } else {
            $field.show();
        }
    });
    
    $container.find('#modelFile').on('change', function(e) {
        var file = e.target.files[0];
        var $info = $container.find('#modelFileInfo');
        
        if (file) {
            selectedModelFile = file;
            var sizeKB = (file.size / 1024).toFixed(1);
            $info.text(file.name + ' (' + sizeKB + ' KB)').show();
            
            uploadModel(file);
        } else {
            selectedModelFile = null;
            $info.hide();
        }
    });
    
    function uploadModel(file) {
        showStatus('Validando y cargando modelo...', 'info');
        
        var formData = new FormData();
        formData.append('file', file);
        
        var xhr = new XMLHttpRequest();
        
        xhr.addEventListener('load', function() {
            if (xhr.status === 200) {
                var response = JSON.parse(xhr.responseText);
                var msg = 'Modelo cargado: ' + response.model_type;
                if (response.trained) {
                    msg += ' (entrenado)';
                    if (response.metrics && response.metrics.rmsecv) {
                        msg += ' | RMSECV=' + response.metrics.rmsecv + ' mg/L';
                    }
                }
                showStatus(msg, 'success');
                loadModelInfo();
            } else {
                var errorMsg = 'Error al cargar el modelo';
                try {
                    var resp = JSON.parse(xhr.responseText);
                    if (resp.detail) errorMsg = resp.detail;
                } catch(e) {}
                showStatus(errorMsg, 'error');
            }
            $container.find('#modelUploadField').hide();
            $container.find('#modelFile').val('');
            $container.find('#modelFileInfo').hide();
            selectedModelFile = null;
        });
        
        xhr.addEventListener('error', function() {
            showStatus('Error de red al cargar el modelo.', 'error');
            $container.find('#modelUploadField').hide();
        });
        
        xhr.open('POST', 'http://localhost:8000/api/model/upload');
        xhr.send(formData);
    }
    
    function loadModelInfo() {
        var xhr = new XMLHttpRequest();
        xhr.open('GET', 'http://localhost:8000/api/model/info');
        
        xhr.addEventListener('load', function() {
            if (xhr.status === 200) {
                var info = JSON.parse(xhr.responseText);
                
                var $status = $container.find('#modelStatus');
                var $type = $container.find('#modelType');
                var $baseline = $container.find('#modelBaseline');
                var $samples = $container.find('#modelSamples');
                var $metricsRow = $container.find('#modelMetricsRow');
                var $metrics = $container.find('#modelMetrics');
                
                if (info.is_trained) {
                    $status.text('Entrenado').css('color', '#4CAF50');
                    $type.text(info.model_type);
                    $baseline.text(info.has_baseline ? 'Si' : 'No');
                    $samples.text(info.n_samples);
                    
                    if (info.model_type === 'PLSR' && info.r2_train !== undefined) {
                        var metricsText = 'R2=' + info.r2_train + ' RMSECV=' + info.rmsecv + ' mg/L (n=' + info.n_components + ' comp)';
                        $metrics.text(metricsText);
                        $metricsRow.show();
                    } else if (info.model_type === 'DEVIATION') {
                        var metricsText = info.n_samples + ' muestras, ' + info.n_features + ' bandas';
                        $metrics.text(metricsText);
                        $metricsRow.show();
                    }
                } else if (info.has_baseline) {
                    $status.text('Calibrado (no entrenado)').css('color', '#FF9800');
                    $type.text('-');
                    $baseline.text('Si');
                    $samples.text(info.n_samples || '0');
                    $metricsRow.hide();
                } else {
                    $status.text('Sin calibrar').css('color', '#f44336');
                    $type.text('-');
                    $baseline.text('No');
                    $samples.text('0');
                    $metricsRow.hide();
                }
            }
        });
        
        xhr.send();
    }
    
    function sendRPC(method, params) {
        var sub = self.ctx.defaultSubscription;
        var entityId = sub.entityId;
        var deviceId = (typeof entityId === 'string') ? entityId : entityId.id;
        
        self.ctx.controlApi.sendOneWayCommand(deviceId, method, params, 5000).then(function() {
            showStatus('Configuracion enviada. El ESP32 se reiniciara en 2s.', 'success');
        }).catch(function(err) {
            showStatus('Error: ' + err, 'error');
        });
    }
    
    function showStatus(msg, type) {
        var $el = $container.find('#status');
        $el.text(msg).removeClass('success error').addClass(type).css('display', 'block');
        setTimeout(function() { $el.css('display', 'none'); }, 8000);
    }
};
