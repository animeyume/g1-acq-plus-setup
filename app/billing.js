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

function query(ids) {
  return new Promise(function(resolve, reject) {
    var timer = setTimeout(function() { reject(new Error('Google Play query timed out')); }, 45000);
    function finish(value) { clearTimeout(timer); resolve(value); }
    Java.perform(function() {
      try {
        if (!ready) throw new Error('BillingClient is not ready');
        if (!Array.isArray(ids) || ids.length < 1 || ids.length > 20) throw new Error('Expected 1..20 product IDs');
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

function currentActivity() {
  factory(null);
  var thread = Java.use('android.app.ActivityThread').currentActivityThread();
  var iterator = thread.mActivities.value.values().iterator();
  while (iterator.hasNext()) {
    var record = iterator.next();
    var activity = record.activity.value;
    if (activity && !activity.isFinishing() && !record.paused.value) return activity;
  }
  return null;
}

function openCheckout(key) {
  return new Promise(function(resolve, reject) {
    Java.perform(function() {
      try {
        if (!ready || !cache[key]) throw new Error('Refresh the selected offer first');
        Java.scheduleOnMainThread(function() {
          try {
            var activity = currentActivity();
            if (!activity) throw new Error('Open Google One in the emulator and retry');
            factory(loader);
            var PDP = Java.use('com.android.billingclient.api.BillingFlowParams$ProductDetailsParams');
            var Flow = Java.use('com.android.billingclient.api.BillingFlowParams');
            var items = Java.use('java.util.ArrayList').$new();
            items.add(PDP.newBuilder().setProductDetails(cache[key].product).setOfferToken(cache[key].token).build());
            var result = client.launchBillingFlow(Java.cast(activity, Java.use('android.app.Activity')), Flow.newBuilder().setProductDetailsParamsList(items).build());
            resolve({code: result.getResponseCode(), message: String(result.getDebugMessage() || '')});
          } catch(error) { reject(error); }
        });
      } catch(error) { reject(error); }
    });
  });
}

rpc.exports = {status: function() { return {ready: ready}; }, query: query, openCheckout: openCheckout};
setImmediate(function() { Java.perform(function() {
  try { initialize(); } catch(error) { event('ready', {code: -1, message: String(error)}); }
}); });
