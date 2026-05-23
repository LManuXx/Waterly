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
        var el = $container.find('#status');
        el.text(msg).removeClass('success error').addClass(type).css('display', 'block');
        setTimeout(function() { el.css('display', 'none'); }, 8000);
    }
};
