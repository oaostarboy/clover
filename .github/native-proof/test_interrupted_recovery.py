import unittest
from interrupted_recovery import check_sample

class RecoverySampleTests(unittest.TestCase):
    def test_requires_exact_old_replacement_and_listener_identity(self):
        good = {'pid': 88, 'birth': 123, 'listeners':[88], 'head':'new', 'code_sha':'new', 'health':'ok'}
        check_sample(good, old_pid=77, old_birth=122, accepted_heads={'old','new'})
        for change in ({'pid':77}, {'birth':122}, {'listeners':[88,99]}, {'head':'bad'}, {'code_sha':'old'}, {'health':'bad'}):
            with self.assertRaises(ValueError): check_sample({**good, **change}, old_pid=77, old_birth=122, accepted_heads={'old','new'})

if __name__ == '__main__': unittest.main()
