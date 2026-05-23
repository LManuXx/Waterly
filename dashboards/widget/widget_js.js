self.onInit = function() {
    var $container = self.ctx.$container;
    
    $container.find('#btn-save').on('click', function() {
        var config = {};
        var ssid = $container.find('#wifi_ssid').val().trim();
        if (ssid) config.wifi_ssid = ssid;
        var pass = $container.find('#wifi_pass').val();
        if (pass) config.wifi_pass = pass;
        var broker = $container.find('#mqtt_broker').val().trim();
        if (broker) config.mqtt_broker = broker;
        var gain = $container.find('#sensor_gain').val();
        if (gain !== '') config.sensor_gain = parseInt(gain);
        var integration = $container.find('#sensor_integration').val();
        if (integration) config.sensor_integration = parseInt(integration);
        var led = $container.find('#sensor_led_current').val();
        if (led !== '') config.sensor_led_current = parseInt(led);
        if (Object.keys(config).length === 0) {
            showStatus('Rellena al menos un campo', 'error');
            return;
        }
        showStatus('Enviando...', 'success');
        sendRPC('saveConfig', config);
    });
    
    $container.find('#btn-reset').on('click', function() {
        if (!confirm('¿Borrar toda la configuracion?')) return;
        showStatus('Enviando Factory Reset...', 'error');
        sendRPC('factoryReset', {});
    });
    
    function sendRPC(method, params) {
        var sub = self.ctx.defaultSubscription;
        var entityId = sub.entityId;
        // entityId puede ser string o objeto {id: "...", entityType: "..."}
        var deviceId = (typeof entityId === 'string') ? entityId : entityId.id;
        
        self.ctx.controlApi.sendOneWayCommand(deviceId, method, params, 5000).then(function() {
            showStatus('Config enviada. ESP32 reiniciando...', 'success');
        }).catch(function(err) {
            showStatus('Error: ' + err, 'error');
        });
    }
    
    function showStatus(msg, type) {
        var el = $container.find('#status');
        el.text(msg).css({
            display: 'block',
            background: type === 'error' ? '#FFEBEE' : '#E8F5E9',
            color: type === 'error' ? '#C62828' : '#2E7D32'
        });
        setTimeout(function() { el.css('display', 'none'); }, 8000);
    }
};
