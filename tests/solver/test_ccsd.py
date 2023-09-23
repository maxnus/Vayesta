import pytest
import unittest

import pyscf
import pyscf.cc

import vayesta
import vayesta.ewf

from tests.common import TestCase
from tests import systems


@pytest.mark.fast
class TestSolvers(TestCase):
    def _test(self, key):
        mf = getattr(getattr(systems, key[0]), key[1])()

        emb = vayesta.ewf.EWF(mf, solver="CCSD", bath_options=dict(bathtype="full"))
        emb.kernel()

        cc = pyscf.cc.CCSD(mf)
        cc.kernel()

        self.assertAlmostEqual(emb.e_corr, cc.e_corr)
        self.assertAlmostEqual(emb.e_tot, cc.e_tot)

    def test_rccsd_h2(self):
        return self._test(("h2_ccpvdz", "rhf"))

    def test_rccsd_h2_df(self):
        return self._test(("h2_ccpvdz_df", "rhf"))

    def test_uccsd_h3(self):
        return self._test(("h3_ccpvdz", "uhf"))

    def test_uccsd_h3_df(self):
        return self._test(("h3_ccpvdz_df", "uhf"))


if __name__ == "__main__":
    print("Running %s" % __file__)
    unittest.main()
