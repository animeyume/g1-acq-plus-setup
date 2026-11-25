const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function list(items = []) {
  return {items, add(value) { this.items.push(value); }, size() { return this.items.length; }, get(i) { return this.items[i]; }};
}
function builder() {
  const data = {};
  return new Proxy({}, {get(target, key) {
    if (key === 'build') return () => data;
    return (value) => { data[key] = value; return target.proxy; };
  }, set(target, key, value) { target[key] = value; return true; }});
}
function makeBuilder() {
  const data = {};
  const value = new Proxy({}, {get(target, key) {
    if (key === 'build') return () => data;
    return argument => { data[key] = argument; return value; };
  }});
  return value;
}
const events = [], calls = [];
const result = {getResponseCode: () => 0, getDebugMessage: () => ''};
let stateListener;
const client = {
  startConnection(value) { stateListener = value; value.onBillingSetupFinished(result); },
  queryProductDetailsAsync(params, listener) {
    const ids = params.setProductList.items.map(x => x.setProductId);
    const details = ids.map(id => ({
      getProductId: () => id, getTitle: () => 'Google AI Pro',
      getSubscriptionOfferDetails: () => list(['trial', null].map(offer => ({
        getOfferId: () => offer, getBasePlanId: () => 'monthly', getOfferToken: () => 'FRESH_TOKEN_' + offer,
        getPricingPhases: () => ({getPricingPhaseList: () => list([{
          getFormattedPrice: () => offer ? 'Free' : '$19.99', getPriceCurrencyCode: () => 'USD',
          getPriceAmountMicros: () => offer ? 0 : 19990000, getBillingPeriod: () => offer ? 'P1Y' : 'P1M',
          getBillingCycleCount: () => offer ? 1 : 0, getRecurrenceMode: () => offer ? 2 : 1
        }])})
      })))
    }));
    listener.onProductDetailsResponse(result, list(details));
  },
  launchBillingFlow(activity, params) { calls.push(params); return result; }
};
const context = {getCodeCacheDir: () => ({getAbsolutePath: () => '/cache'}), getClassLoader: () => ({})};
const classes = {
  'android.app.ActivityThread': {
    currentApplication: () => ({getApplicationContext: () => context}),
    currentActivityThread: () => ({mActivities: {value: {values: () => ({iterator: () => {
      let done = false; return {hasNext: () => !done, next: () => {done = true; return {activity: {value: {isFinishing: () => false}}, paused: {value: false}}; }};
    }})}}})
  },
  'dalvik.system.DexClassLoader': {$new: () => ({})},
  'com.android.billingclient.api.BillingClient': {newBuilder: () => ({
    setListener() { return this; }, enablePendingPurchases() { return this; }, build: () => client
  })},
  'com.android.billingclient.api.QueryProductDetailsParams$Product': {newBuilder: makeBuilder},
  'com.android.billingclient.api.QueryProductDetailsParams': {newBuilder: makeBuilder},
  'com.android.billingclient.api.BillingFlowParams$ProductDetailsParams': {newBuilder: makeBuilder},
  'com.android.billingclient.api.BillingFlowParams': {newBuilder: makeBuilder},
  'java.util.ArrayList': {$new: () => list()},
};
for (const name of ['PurchasesUpdatedListener', 'BillingClientStateListener', 'ProductDetailsResponseListener', 'ProductDetails', 'ProductDetails$SubscriptionOfferDetails', 'ProductDetails$PricingPhase']) {
  classes['com.android.billingclient.api.' + name] = {};
}
classes['android.app.Activity'] = {};
classes['android.app.ActivityThread$ActivityClientRecord'] = {};
const sandbox = {send: x => events.push(x), rpc: {exports: {}}, setImmediate, setTimeout, clearTimeout,
  Java: {classFactory: {}, perform: fn => fn(), retain: x => x, cast: x => x, scheduleOnMainThread: fn => fn(),
    use(name) { assert.ok(name in classes, 'Unknown Java class: ' + name); return classes[name]; },
    registerClass(spec) { return {$new: () => ({...spec.methods})}; }
  }};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../app/billing.js'), 'utf8'), sandbox);
(async () => {
  await new Promise(setImmediate);
  assert.equal(events[0].event, 'ready'); assert.equal(events[0].code, 0);
  await assert.rejects(sandbox.rpc.exports.openCheckout('missing'), /Обновите/);
  assert.equal(calls.length, 0);
  const data = await sandbox.rpc.exports.query(['g1.2tb.ai.12months_eft']);
  assert.equal(data.code, 0); assert.equal(data.offers.length, 2);
  assert.equal(data.offers[0].phases[0].currency, 'USD');
  assert.equal(data.offers[0].phases[0].period, 'P1Y');
  assert.equal(data.offers[1].offer, '');
  assert.equal(JSON.stringify(data).includes('FRESH_TOKEN'), false);
  assert.equal(calls.length, 0, 'Catalog reads must not open checkout');
  await assert.rejects(sandbox.rpc.exports.query(Array(21).fill('g1.2tb.ai')), /1 до 20/);
  const opened = await sandbox.rpc.exports.openCheckout(data.offers[0].key);
  assert.equal(opened.code, 0); assert.equal(calls.length, 1);
  assert.equal(calls[0].setProductDetailsParamsList.items[0].setOfferToken, 'FRESH_TOKEN_trial');
  stateListener.onBillingServiceDisconnected();
  await assert.rejects(sandbox.rpc.exports.query(['g1.2tb.ai']), /не готов/);
  stateListener.onBillingSetupFinished(result);
  classes['android.app.ActivityThread'].currentActivityThread = () => ({});
  const noWindow = await sandbox.rpc.exports.openCheckout(data.offers[0].key);
  assert.equal(noWindow.code, -2, 'missing mActivities must return -2, not TypeError');
  console.log('Billing bridge: 12 offline checks passed. No Android transaction executed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
