import unittest
from interrupted_controller import select_watchers, Refusal

class WatcherSelectionTests(unittest.TestCase):
    def test_exact_product_launches_select_two_distinct_roles(self):
        events=[{'stage':'launch','pid':50,'child_pid':51,'owner':'_cmd_update_impl','argv':['/python','-I','-S','/fixture/watcher.py','/fixture/home/beacon']}, {'stage':'launch','pid':50,'child_pid':52,'owner':'_spawn_gateway_restart_watcher','argv':['/python','-c','trusted-code','40','/python','-m','clover_cli.main','gateway','run']}]
        self.assertEqual(set(select_watchers(events,50,40,'/fixture/home/beacon')),{'rollback','restart'})
        for broken in [events[:1],events+[events[0]], [{**events[0],'pid':99},events[1]], [events[0],{**events[1],'argv':['/python','-c','code','41']}]]:
            with self.assertRaises(Refusal): select_watchers(broken,50,40,'/fixture/home/beacon')

    def test_pre_pause_watcher_is_selected(self):
        rollback = {'stage':'launch','pid':50,'child_pid':51,'owner':'_arm_restart_watcher_before_pause','argv':['/python','/fixture/home/beacon']}
        restart = {'stage':'launch','pid':50,'child_pid':52,'owner':'_spawn_gateway_restart_watcher','argv':['/python','-c','code','40']}
        self.assertEqual(select_watchers([rollback, restart],50,40,'/fixture/home/beacon')['rollback']['child_pid'],51)

    def test_hosted_preflight_rejects_non_hosted_environment(self):
        from interrupted_controller import hosted_preflight
        with self.assertRaises(Refusal):
            hosted_preflight({}, '/tmp', '/tmp/home', '/tmp/home/clover-c1', '/tmp/evidence')

    def test_unrelated_launches_are_ignored_and_roles_must_be_distinct(self):
        rollback = {'stage':'launch','pid':50,'child_pid':51,'owner':'_cmd_update_impl','argv':['/python','/fixture/home/beacon']}
        restart = {'stage':'launch','pid':50,'child_pid':52,'owner':'_spawn_gateway_restart_watcher','argv':['/python','-c','code','40']}
        unrelated = {'stage':'launch','pid':50,'child_pid':53,'owner':'_cmd_update_impl','argv':['git','status']}
        selected = select_watchers([unrelated, rollback, restart],50,40,'/fixture/home/beacon')
        self.assertEqual(selected['rollback']['child_pid'],51)
        with self.assertRaises(Refusal):
            select_watchers([rollback, {**restart, 'child_pid':51}],50,40,'/fixture/home/beacon')

if __name__=='__main__':unittest.main()
