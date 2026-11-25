/* Android adapter for published G1 ACQ Plus product IDs. No purchases are auto-confirmed. */
'use strict';
var loader = null, context = null, client = null, ready = false;
var keep = [], cache = Object.create(null);
var nonce = 'g1plus.v1.C' + Date.now();
function hold(x) { keep.push(x); return x; }
function event(name, data) { send(Object.assign({event: name}, data || {})); }
function factory(value) { Java.classFactory.loader = value; }

function initialize() {
  factory(null);
  context = Java.use('android.app.ActivityThread').currentApplication().getApplicationContext();
  var Dex = Java.use('dalvik.system.DexClassLoader');
  loader = hold(Dex.$new('/data/local/tmp/g1plus-billing.apk', context.getCodeCacheDir().getAbsolutePath(), null, context.getClassLoader()));
  factory(loader);
  var Billing = Java.use('com.android.billingclient.api.BillingClient');
  var Update = Java.registerClass({name: nonce + 'Purchases',
    implements: [Java.use('com.android.billingclient.api.PurchasesUpdatedListener')],
    methods: {onPurchasesUpdated: function(result, purchases) {
      event('purchase_result', {code: result.getResponseCode(), message: String(result.getDebugMessage() || '')});
    }}
  });
  var builder = Billing.newBuilder(context).setListener(hold(Update.$new()));
  builder.enablePendingPurchases();
  client = hold(builder.build());
  var State = Java.registerClass({name: nonce + 'State',
    implements: [Java.use('com.android.billingclient.api.BillingClientStateListener')],
    methods: {
      onBillingSetupFinished: function(result) {
        ready = result.getResponseCode() === 0;
        event('ready', {code: result.getResponseCode(), message: String(result.getDebugMessage() || '')});
      },
      onBillingServiceDisconnected: function() { ready = false; event('disconnected'); }
    }
  });
  client.startConnection(hold(State.$new()));
}

function ensureReady() {
  return new Promise(function(resolve, reject) {
    if (ready) { resolve(true); return; }
    var started = Date.now();
    (function poll() {
      if (ready) { resolve(true); return; }
      if (Date.now() - started > 20000) { reject(new Error('BillingClient не готов: Google Play не отвечает.')); return; }
      setTimeout(poll, 250);
    })();
  });
}

function query(ids) {
  return new Promise(function(resolve, reject) {
    var timer = setTimeout(function() { reject(new Error('Google Play не ответил на запрос за 45 секунд.')); }, 45000);
    function finish(value) { clearTimeout(timer); resolve(value); }
    Java.perform(function() {
      try {
        if (!ready) throw new Error('BillingClient не готов.');
        if (!Array.isArray(ids) || ids.length < 1 || ids.length > 20) throw new Error('Ожидалось от 1 до 20 идентификаторов.');
        factory(loader);
        var Product = Java.use('com.android.billingclient.api.QueryProductDetailsParams$Product');
        var Params = Java.use('com.android.billingclient.api.QueryProductDetailsParams');
        var list = Java.use('java.util.ArrayList').$new();
        ids.forEach(function(id) { list.add(Product.newBuilder().setProductId(id).setProductType('subs').build()); });
        var Response = Java.registerClass({name: nonce + 'Query' + Date.now(),
          implements: [Java.use('com.android.billingclient.api.ProductDetailsResponseListener')],
          methods: {onProductDetailsResponse: function(result, details) {
            try {
              factory(loader);
              var output = {code: result.getResponseCode(), message: String(result.getDebugMessage() || ''), offers: []};
              var PD = Java.use('com.android.billingclient.api.ProductDetails');
              var OD = Java.use('com.android.billingclient.api.ProductDetails$SubscriptionOfferDetails');
              var Phase = Java.use('com.android.billingclient.api.ProductDetails$PricingPhase');
              for (var i = 0; details && i < details.size(); i++) {
                var pd = Java.cast(details.get(i), PD);
                var sku = String(pd.getProductId());
                var offers = pd.getSubscriptionOfferDetails();
                for (var n = 0; offers && n < offers.size(); n++) {
                  var offer = Java.cast(offers.get(n), OD);
                  var id = String(offer.getOfferId() || '');
                  var base = String(offer.getBasePlanId());
                  var key = sku + '|' + base + '|' + id;
                  var phases = offer.getPricingPhases().getPricingPhaseList();
                  var row = {key: key, product: sku, title: String(pd.getTitle()), base_plan: base, offer: id, phases: []};
                  for (var p = 0; p < phases.size(); p++) {
                    var phase = Java.cast(phases.get(p), Phase);
                    row.phases.push({price: String(phase.getFormattedPrice()), currency: String(phase.getPriceCurrencyCode()),
                      micros: String(phase.getPriceAmountMicros()), period: String(phase.getBillingPeriod()),
                      cycles: phase.getBillingCycleCount(), recurrence: phase.getRecurrenceMode()});
                  }
                  cache[key] = {product: Java.retain(pd), token: String(offer.getOfferToken())};
                  output.offers.push(row);
                }
              }
              finish(output);
            } catch(error) { clearTimeout(timer); reject(error); }
          }}
        });
        client.queryProductDetailsAsync(Params.newBuilder().setProductList(list).build(), hold(Response.$new()));
      } catch(error) { clearTimeout(timer); reject(error); }
    });
  });
}

/* Field access never throws: Frida returns undefined for a field the running
   Android build does not expose, and \"undefined.value\" was the old crash. */
function fieldValue(target, name) {
  if (!target) return null;
  try {
    var field = target[name];
    if (field === undefined || field === null) return null;
    return field.value;
  } catch (error) { return null; }
}

function mapValues(map) {
  try { return map.values(); } catch (error) {
    return Java.cast(map, Java.use('java.util.Map')).values();
  }
}

function threadInstance() {
  factory(null);
  var thread = null;
  try { thread = Java.use('android.app.ActivityThread').currentActivityThread(); } catch (error) { thread = null; }
  if (thread) return thread;
  try {
    Java.choose('android.app.ActivityThread', {onMatch: function (instance) { thread = instance; return 'stop'; }});
  } catch (error) { thread = null; }
  return thread;
}

/* Every window of the hooked Google One process, newest state first. */
function activityList() {
  var result = [];
  var thread = threadInstance();
  if (!thread) return result;
  var map = fieldValue(thread, 'mActivities');
  if (!map) return result;
  try {
    var Record = Java.use('android.app.ActivityThread$ActivityClientRecord');
    var Activity = Java.use('android.app.Activity');
    var values = mapValues(map);
    if (!values) return result;
    var iterator = values.iterator();
    while (iterator.hasNext()) {
      var record = null, activity = null;
      try { record = Java.cast(iterator.next(), Record); } catch (error) { record = null; }
      if (!record) continue;
      try { activity = Java.cast(fieldValue(record, 'activity'), Activity); } catch (error) { activity = null; }
      if (!activity) continue;
      var finishing = false;
      try { finishing = activity.isFinishing(); } catch (error) { finishing = false; }
      if (finishing) continue;
      var focused = false;
      try { focused = activity.hasWindowFocus(); } catch (error) { focused = false; }
      var name = '';
      try { name = String(activity.getClass().getName()); } catch (error) { name = ''; }
      result.push({instance: activity, paused: fieldValue(record, 'paused') === true, focused: focused, name: name});
    }
  } catch (error) { }
  return result;
}

function pickActivity() {
  var list = activityList();
  var i;
  for (i = 0; i < list.length; i++) {
    if (!list[i].paused && list[i].focused) return list[i];
  }
  for (i = 0; i < list.length; i++) {
    if (!list[i].paused) return list[i];
  }
  return list.length ? list[0] : null;
}

function currentActivity() {
  var picked = pickActivity();
  return picked ? picked.instance : null;
}

function openCheckout(key) {
  return ensureReady().then(function() { return new Promise(function(resolve, reject) {
    Java.perform(function() {
      try {
        if (!cache[key]) throw new Error('Предложение не загружено. Обновите список клавишей r.');
        Java.scheduleOnMainThread(function() {
          try {
            var picked = pickActivity();
            if (!picked) {
              resolve({code: -2, message: 'В процессе Google One нет активного окна.', activity: '', focused: false});
              return;
            }
            factory(loader);
            var PDP = Java.use('com.android.billingclient.api.BillingFlowParams$ProductDetailsParams');
            var Flow = Java.use('com.android.billingclient.api.BillingFlowParams');
            var items = Java.use('java.util.ArrayList').$new();
            items.add(PDP.newBuilder().setProductDetails(cache[key].product).setOfferToken(cache[key].token).build());
            var result = client.launchBillingFlow(Java.cast(picked.instance, Java.use('android.app.Activity')),
              Flow.newBuilder().setProductDetailsParamsList(items).build());
            resolve({code: result.getResponseCode(), message: String(result.getDebugMessage() || ''),
              activity: picked.name, focused: picked.focused});
          } catch(error) { reject(error); }
        });
      } catch(error) { reject(error); }
    });
  }); });
}

rpc.exports = {
  status: function() { return {ready: ready}; },
  activity: function() { var out = []; Java.perform(function() { var list = activityList(); for (var i = 0; i < list.length; i++) out.push({name: list[i].name, paused: list[i].paused, focused: list[i].focused}); }); return out; },
  query: query,
  openCheckout: openCheckout
};
setImmediate(function() { Java.perform(function() {
  try { initialize(); } catch(error) { event('ready', {code: -1, message: String(error)}); }
}); });
