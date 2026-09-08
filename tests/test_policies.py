import json
import math
import unittest
from dataclasses import replace

from behavior_policy import (DriverTraits, EvolutionTraits, PersonProfile, RiderPolicy,
                             RiderTraits, OpenApp, OrderQuote, Stop)
from main import Simulation
from marketplace_engine import CommandRejected, World
from marketplace_policy import (Campaign, MarketplaceParameters, MarketplacePolicy, PlatformPolicy)
from policy_contracts import RandomValues, freeze, plain


APPS = ('rebu', 'blot', 'flyt')


def profile(apps=APPS, **kwargs):
    return PersonProfile(apps=apps, preferred_app=apps[0], awareness=APPS,
                         rider=RiderTraits(purchase_bias=20, taste_scale=0),
                         driver=DriverTraits(acceptance_bias=100), **kwargs)


def sim(drivers=2, riders=5, **kwargs):
    return Simulation(driver_count=drivers, rider_count=riders,
                      rider_profiles=kwargs.pop('rider_profiles', profile()),
                      driver_profiles=kwargs.pop('driver_profiles', profile()), **kwargs)


class MarketplaceTests(unittest.TestCase):
    def test_independent_tariffs_rules_and_strict_validation(self):
        a = PlatformPolicy.compile(defaults={'base_fare_minor': 100}, overrides={'base_fare_minor': 200},
            rules=[{'id': 'vip', 'priority': 2, 'when': {'segment': 'vip'}, 'parameters': {'base_fare_minor': 400}},
                   {'id': 'new', 'priority': 1, 'when': {'new_user': True}, 'parameters': {'per_km_minor': 10}}])
        self.assertEqual(a.resolve({'segment': 'vip', 'new_user': True})[0].per_km_minor, 150)
        self.assertEqual(a.resolve({'segment': 'vip'})[0].base_fare_minor, 400)
        self.assertEqual(a.resolve({})[0].base_fare_minor, 200)
        for kwargs in ({'overrides': {'unknown': 1}}, {'overrides': {'max_local_commitments': 3}},
                       {'overrides': {'retry_seconds': 0}}, {'fallback': ''},
                       {'rules': [{'id': 'bad', 'priority': 1, 'when': {'hidden_trait': True}, 'parameters': {}}]}):
            with self.assertRaises((ValueError, TypeError)):
                PlatformPolicy.compile(**kwargs)
        s = sim(platforms={'rebu': a, 'blot': PlatformPolicy.compile(overrides={'base_fare_minor': 900}), 'flyt': PlatformPolicy()})
        intent = s.engine.begin_intent(s.riders[0], (0,0), (1,0))
        for p in ('rebu','blot'):
            s.engine.open_app('rider',s.riders[0],p)
        self.assertEqual([q.fare.gross_minor for q in s.engine.quotes.values()], [210,1050])

    def test_read_only_and_hidden_competitor_invariance(self):
        s = sim()
        d = s.drivers[0]
        s.engine.start_shift(d,(0,0))
        intent = s.engine.begin_intent(s.riders[0], (0,0),(5,0))
        s.engine.open_app('rider',s.riders[0],'rebu')
        q = next(iter(s.engine.quotes.values()))
        order = s.engine.place_order(intent.id,q.id)
        offer = s.engine.create_offer('rebu',order.id,d,1000,expires_at=10)
        s.engine.respond_to_offer(offer.id,True)
        s.engine.open_app('driver',d,'blot')
        other = s.engine.begin_intent(s.riders[1],(1,0),(2,0))
        s.engine.open_app('rider',s.riders[1],'blot')
        req = s.engine.platform_view('blot').rider_request(s.riders[1])
        c1 = s.policies.platform_context('blot',request=req)
        with self.assertRaises(TypeError):
            c1.now = 40
        with self.assertRaises(TypeError):
            c1.drivers[0].visible['new_user'] = False
        self.assertEqual(c1.drivers[0].own_order_ids, ())
        result1 = s.policies.platforms['blot'].quote(c1,freeze({}),RandomValues(0,('test',)))
        order.destination = (1000,1000)
        c2 = s.policies.platform_context('blot',request=req)
        result2 = s.policies.platforms['blot'].quote(c2,freeze({}),RandomValues(0,('test',)))
        self.assertEqual(result1, result2)
        own = s.policies.platform_context('rebu',request=s.engine.platform_view('rebu').rider_request(s.riders[0]))
        self.assertGreater(s.policies.platforms['rebu'].pickup_eta(own,own.drivers[0],(1,0),MarketplaceParameters()),
                           result1.action.eta_seconds)

    def test_quote_expiry_half_open(self):
        s=sim(drivers=0,riders=1)
        intent=s.engine.begin_intent(s.riders[0],(0,0),(1,0))
        s.engine.open_app('rider',s.riders[0],'rebu')
        q=s.engine.issue_quote('rebu',intent.id,100,distance_km=1,duration_seconds=120,
                               eta_seconds=0,expires_at=1)
        s.advance_to(1)
        with self.assertRaises(CommandRejected):
            s.engine.place_order(intent.id,q.id)

    def test_rounding_campaign_tie_and_committed_bonus(self):
        cfg=PlatformPolicy.compile(overrides={'base_fare_minor': 101,'per_km_minor':0,'commission_fraction': .5},
            campaigns=[{'id':'z','end':100,'discount_fraction':.5,'bonus_minor':200},
                       {'id':'a','end':100,'discount_fraction':.5,'bonus_minor':200}])
        s=sim(drivers=1,riders=1, platforms={p:cfg for p in APPS})
        s.schedule_driver_session(0,s.drivers[0],(0,0))
        s.schedule_rider_session(0,s.riders[0],(0,0),(1,0))
        s.advance_to(16)
        offer=next(iter(s.engine.offers.values()))
        self.assertEqual((offer.payout.payout_minor,offer.payout.bonus_minor,offer.campaign_id),(51,200,'a'))
        quote=s.engine.quotes[1]
        self.assertEqual((quote.fare.discount_minor,quote.campaign_id),(51,'a'))
        s.policies.schedule_intervention(20,platform_id='rebu',config=PlatformPolicy(version='v2'))
        s.advance_to(200)
        settlement=next(iter(s.engine.settlements.values()))
        self.assertEqual((settlement.rider_payment_minor,settlement.driver_payout_minor,
                          settlement.platform_contribution_minor),(50,251,-201))
        self.assertFalse(cfg.campaigns[0].eligible(100,{}))

    def test_distinct_retries_and_expiry_responses(self):
        dp=replace(profile(),driver=DriverTraits(response_seconds=10,acceptance_bias=100))
        s=sim(drivers=6,riders=1,driver_profiles=dp,
              rider_profiles=replace(profile(('rebu',)),rider=RiderTraits(purchase_bias=20,taste_scale=0)))
        for d in s.drivers:
            s.schedule_driver_session(0,d,(0,0))
        s.schedule_rider_session(0,s.riders[0],(0,0),(1,0))
        s.advance_to(150)
        self.assertEqual(len(s.engine.offers),5)
        self.assertEqual(len({o.driver_id for o in s.engine.offers.values()}),5)
        self.assertTrue(all(o.state=='expired' for o in s.engine.offers.values()))
        self.assertEqual(s.engine.intents[1].outcome,'abandoned')

    def test_access_install_account_and_registration_are_distinct(self):
        s=sim(drivers=1,riders=0,driver_profiles=profile(('rebu',)))
        d=s.drivers[0]
        s.engine.start_shift(d,(0,0))
        s.engine.install_app('driver',d,'blot')
        with self.assertRaises(CommandRejected): s.engine.open_app('driver',d,'blot')
        s.engine.activate_account('driver',d,'blot')
        with self.assertRaises(CommandRejected): s.engine.open_app('driver',d,'blot')
        s.engine.register_car(d,'blot')
        s.engine.open_app('driver',d,'blot')
        self.assertIn('blot',s.engine.drivers[d].open_apps)
        self.assertEqual(s.policies.state('driver',d)['preferred_app'],'rebu')


class BehaviorTests(unittest.TestCase):
    def test_all_access_subsets(self):
        from itertools import combinations
        for size in (1,2,3):
            for apps in combinations(APPS,size):
                s=sim(drivers=1,riders=1,rider_profiles=profile(apps),driver_profiles=profile(apps))
                s.schedule_driver_session(0,s.drivers[0],(0,0),shift_seconds=1000)
                s.schedule_rider_session(0,s.riders[0],(0,0),(1,0))
                s.advance_to(1100)
                self.assertEqual(s.engine.intents[1].outcome,'completed')
                self.assertEqual(len(s.engine.settlements),1)

    def test_identical_quote_choice_and_isolated_triggers(self):
        t=RiderTraits(purchase_bias=10,price_sensitivity=10,eta_sensitivity=10,taste_scale=0)
        context={'now':10,'intent':{'id':1,'created_at':0,'origin':(0,0),'destination':(1,0)},
                 'usable_apps': APPS, 'preferred_app':'rebu','scores':{},'announced':{},
                 'quotes':[{'id':1,'at':0,'platform_id':'rebu','expires_at':30,
                            'fare':{'gross_minor':350,'discount_minor':0},'eta_seconds':0}]}
        memory={'visited':['rebu'],'attempts':{},'inspection_draw':.5}
        rng=RandomValues(0,('rider',1,1))
        policy=RiderPolicy(t)
        a=policy.decide(freeze(context),freeze(memory),rng)
        self.assertIsInstance(a.action,OrderQuote)
        repeated=json.loads(json.dumps(context))
        repeated['quotes'][0]['id']=99
        b=policy.decide(freeze(repeated),freeze(memory),rng)
        self.assertEqual(a.memory['outside_draw'],b.memory['outside_draw'])
        self.assertEqual(a.memory['taste'],b.memory['taste'])
        self.assertIsInstance(b.action,OrderQuote)
        for key,value in [('eta_seconds',10000),('fare',{'gross_minor':10000,'discount_minor':0}),('eta_seconds',None)]:
            changed=json.loads(json.dumps(context))
            changed['quotes'][0][key]=value
            self.assertIsInstance(policy.decide(freeze(changed),freeze(memory),rng).action,OpenApp)

    def test_cancellation_then_retry_same_intent(self):
        rp=replace(profile(),rider=RiderTraits(purchase_bias=20,taste_scale=0,cancellation_after_seconds=20,
                                              eta_sensitivity=0,price_sensitivity=0))
        s=sim(drivers=1,riders=1,rider_profiles=rp)
        s.engine.start_shift(s.drivers[0],(10,0))
        for p in APPS: s.engine.open_app('driver',s.drivers[0],p)
        s.schedule_rider_session(0,s.riders[0],(0,0),(1,0))
        s.advance_to(500)
        orders=list(s.engine.orders.values())
        self.assertEqual(len(orders),3)
        self.assertEqual(len({o.platform_id for o in orders}),3)
        self.assertTrue(all(o.state=='canceled' for o in orders))
        self.assertEqual(len(s.engine.intents),1)
        self.assertIsNotNone(s.engine.intents[1].converted_at)
        self.assertTrue(all(a.timeline['canceled']<b.created_at for a,b in zip(orders,orders[1:])))
        self.assertEqual(len(s.engine.settlements),0)

    def test_rejected_offer_resets_clock_opening_does_not(self):
        dp=replace(profile(),driver=DriverTraits(acceptance_bias=-100,no_offer_seconds=20,further_opening_seconds=10))
        s=sim(drivers=1,riders=1,driver_profiles=dp)
        d=s.drivers[0]
        s.schedule_driver_session(0,d,(0,0))
        s.schedule_rider_session(0,s.riders[0],(0,0),(1,0))
        s.advance_to(33)
        state=s.policies.state('driver',d)
        self.assertEqual(state['search']['no_offer_since'],12)
        self.assertEqual(len(s.engine.drivers[d].open_apps),2)
        s.advance_to(43)
        self.assertEqual(state['search']['no_offer_since'],12)
        self.assertEqual(len(s.engine.drivers[d].open_apps),3)

    def test_competing_delayed_offers_cap_and_back_to_back(self):
        s=sim(drivers=1,riders=3)
        d=s.drivers[0]
        s.engine.start_shift(d,(0,0))
        for p in APPS: s.engine.open_app('driver',d,p)
        for i,p in enumerate(APPS):
            intent=s.engine.begin_intent(s.riders[i],(0,0),(1,0))
            s.engine.open_app('rider',s.riders[i],p)
            q=next(q for q in s.engine.quotes.values() if q.intent_id==intent.id)
            s.engine.place_order(intent.id,q.id)
        s.advance_to(4)
        self.assertEqual(len(s.engine.drivers[d].commitments),2)
        self.assertEqual([o.state for o in s.engine.offers.values()],['accepted','accepted','rejected'])
        s.advance_to(310)
        self.assertTrue(any(o['type']=='back_to_back_ready' for o in s.policies.observations))
        services=list(s.engine.services.values())
        self.assertEqual(services[0].ended_at,services[1].started_at)
        self.assertEqual(services[1].legs[0].origin,(1,0))
        waits=[o for o in s.policies.observations if o['type']=='commitment_wait']
        self.assertGreater(waits[1]['seconds'],0)

    def test_checkpoint_resume_during_pending_response_and_evolution(self):
        dp=replace(profile(('rebu',)),evolution=EvolutionTraits(adoption_rate_per_day=100,learning_rate=.3,onboard_car=True))
        s=sim(drivers=1,riders=2,driver_profiles=dp)
        s.schedule_driver_session(0,s.drivers[0],(0,0),shift_seconds=2000)
        s.schedule_rider_session(0,s.riders[0],(0,0),(1,0))
        s.schedule_rider_session(200,s.riders[1],(0,0),(1,0))
        s.policies.schedule_checkpoint(1000)
        s.advance_to(13)
        restored=Simulation.restore(json.loads(json.dumps(s.snapshot())))
        s.advance_to(2100)
        restored.advance_to(2100)
        self.assertEqual(json.loads(json.dumps(s.snapshot())),json.loads(json.dumps(restored.snapshot())))
        self.assertGreater(len(s.engine.drivers[s.drivers[0]].apps),1)
        self.assertEqual(s.engine.drivers[s.drivers[0]].apps,s.engine.cars[s.drivers[0]].registrations)

    def test_zero_rates_freeze(self):
        s=sim(drivers=1,riders=1,driver_profiles=profile(('rebu',)),rider_profiles=profile(('rebu',)))
        s.policies.schedule_checkpoint(86400)
        s.advance_to(86400)
        self.assertEqual(s.engine.drivers[s.drivers[0]].apps,{'rebu'})
        self.assertEqual(s.engine.riders[s.riders[0]].apps,{'rebu'})
        self.assertTrue(all(p['scores']=={} and p['preferred_app']=='rebu' for p in s.policies.people.values()))


class AdditionalLifecycleTests(unittest.TestCase):
    def test_queued_cancellation_preserves_active_ride(self):
        s=sim(drivers=1,riders=2)
        d=s.drivers[0]
        s.engine.start_shift(d,(0,0))
        orders=[]
        for i in range(2):
            intent=s.engine.begin_intent(s.riders[i],(0,0),(1,0))
            s.engine.open_app('rider',s.riders[i],'rebu')
            q=next(q for q in s.engine.quotes.values() if q.intent_id==intent.id)
            order=s.engine.place_order(intent.id,q.id)
            offer=s.engine.create_offer('rebu',order.id,d,1000,expires_at=10)
            s.engine.respond_to_offer(offer.id,True)
            orders.append(order)
        from policy_contracts import Cancel
        s.policies.cancel(Cancel(orders[1].id,'rider','changed plans'))
        self.assertEqual(s.engine.drivers[d].commitments,[orders[0].id])
        self.assertEqual(s.engine.services[orders[0].service_id].end_reason,None)
        self.assertEqual(len(s.engine.settlements),0)

    def test_eta_revisions_exclude_target_service(self):
        s=sim(drivers=1,riders=2)
        d=s.drivers[0]
        s.engine.start_shift(d,(0,0))
        orders=[]
        for i in range(2):
            intent=s.engine.begin_intent(s.riders[i],(0,0),(1,0))
            s.engine.open_app('rider',s.riders[i],'rebu')
            q=next(q for q in s.engine.quotes.values() if q.intent_id==intent.id)
            order=s.engine.place_order(intent.id,q.id)
            offer=s.engine.create_offer('rebu',order.id,d,1000,expires_at=10,eta_seconds=999)
            s.engine.respond_to_offer(offer.id,True)
            orders.append(order)
        self.assertEqual(orders[0].eta_predictions[-1]['eta_seconds'],0)
        self.assertEqual(orders[1].eta_predictions[-1]['eta_seconds'],270)
        s.advance_to(150)
        self.assertEqual(orders[1].eta_predictions[-1]['at'],150)
        self.assertEqual(orders[1].eta_predictions[-1]['eta_seconds'],120)
        self.assertNotIn('arrived',orders[1].timeline)

    def test_busy_expansion_and_full_capacity_exposure(self):
        dp=replace(profile(),driver=DriverTraits(acceptance_bias=100,expand_while_busy=True,no_offer_seconds=10))
        s=sim(drivers=1,riders=1,driver_profiles=dp)
        d=s.drivers[0]
        s.schedule_driver_session(0,d,(0,0))
        s.schedule_rider_session(0,s.riders[0],(0,0),(5,0))
        s.advance_to(60)
        self.assertEqual(s.engine.drivers[d].open_apps,set(APPS))
        self.assertTrue(any(o.get('phase')=='busy' and o['type']=='opportunity_exposure'
                            for o in s.policies.observations))

    def test_exclusive_switch_retains_required_app(self):
        from behavior_policy import DriverPolicy, ExpandApps
        policy=DriverPolicy(DriverTraits(expansion='exclusive_switch',expand_while_busy=True))
        context=freeze({'now':100,'exit_requested':False,'free_slots':1,
            'commitments':[{'platform_id':'rebu'}],'usable_apps':APPS,'open_apps':['rebu','blot'],
            'preferred_app':'rebu','scores':{},'announced':{}})
        result=policy.expand(context,freeze({'no_offer_since':0,'visited':['rebu','blot']}),RandomValues(0,('x',)))
        self.assertEqual(result.action,ExpandApps('flyt',('blot',)))

    def test_strict_local_policy_still_sees_cross_platform_busy_driver(self):
        configs={p:PlatformPolicy.compile(overrides={'max_local_commitments':1}) for p in APPS}
        s=sim(drivers=1,riders=2,platforms=configs)
        d=s.drivers[0]
        s.engine.start_shift(d,(0,0))
        s.engine.open_app('driver',d,'blot')
        intent=s.engine.begin_intent(s.riders[0],(0,0),(1,0))
        s.engine.open_app('rider',s.riders[0],'rebu')
        order=s.engine.place_order(intent.id,1)
        offer=s.engine.create_offer('rebu',order.id,d,1000,expires_at=10)
        s.engine.respond_to_offer(offer.id,True)
        s.engine.begin_intent(s.riders[1],(0,0),(1,0))
        for p in ('rebu','blot'):
            s.engine.open_app('rider',s.riders[1],p)
        quotes=list(s.engine.quotes.values())
        self.assertIsNone(quotes[-2].eta_seconds)
        self.assertEqual(quotes[-1].eta_seconds,0)

    def test_zero_learning_with_observations_retains_scores(self):
        s=sim(drivers=0,riders=1,rider_profiles=profile(('rebu',)))
        state=s.policies.state('rider',s.riders[0])
        state['scores']={'rebu':.4}
        s.policies.add_reward('rider',s.riders[0],'rebu',-2,'failure',1)
        s.policies.schedule_checkpoint(1)
        s.advance_to(1)
        self.assertEqual(state['scores'],{'rebu':.4})

    def test_shift_exit_drains_and_json_restores_accounts(self):
        s=sim(drivers=1,riders=1)
        s.schedule_driver_session(0,s.drivers[0],(0,0),shift_seconds=16)
        s.schedule_rider_session(0,s.riders[0],(0,0),(1,0))
        s.advance_to(17)
        self.assertIsNotNone(s.engine.drivers[s.drivers[0]].shift_id)
        r=Simulation.restore(json.loads(json.dumps(s.snapshot())))
        r.advance_to(200)
        self.assertIsNone(r.engine.drivers[r.drivers[0]].shift_id)
        self.assertEqual(r.engine.riders[r.riders[0]].accounts,set(APPS))
        self.assertEqual(r.engine.orders[1].state,'completed')


class EvolutionEstimatorTests(unittest.TestCase):
    def test_old_exposure_does_not_repeatedly_penalize_preferences(self):
        from behavior_policy import EvolutionPolicy
        p=EvolutionPolicy(EvolutionTraits(learning_rate=.2))
        context={'now':1000,'elapsed_days':0,'known_launched_apps':APPS,'apps':APPS,
            'usable_apps':APPS,'preferred_app':'rebu','scores':{},'observations':[],
            'exposure':{'rebu':{'idle':1000,'busy':0}},'offer_counts':{'rebu':{'idle':1,'busy':0}}}
        first=p.checkpoint(freeze(context),freeze({}),RandomValues(0,('e',1)))
        context['now']=2000
        context['scores']=first.memory['scores']
        second=p.checkpoint(freeze(context),freeze(first.memory),RandomValues(0,('e',2)))
        self.assertEqual(first.memory['scores'],second.memory['scores'])


if __name__ == '__main__':
    unittest.main()
