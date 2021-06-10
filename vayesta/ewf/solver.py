import dataclasses
from timeit import default_timer as timer

import numpy as np

import pyscf
import pyscf.cc
import pyscf.pbc

from vayesta.core.util import *


def get_solver_class(solver):
    if solver.upper() in ('CCSD', 'CCSD(T)'):
        return CCSDSolver
    if solver.upper() == 'FCI':
        return FCISolver
    raise NotImplementedError("Unknown solver %s" % solver)


@dataclasses.dataclass
class ClusterSolverOptions(Options):
    eom_ccsd : bool = NotSet
    make_rdm1 : bool = NotSet

class ClusterSolver:
    """Base class for cluster solver"""

    def __init__(self, fragment, mo_coeff, mo_occ, nocc_frozen, nvir_frozen, eris=None,
            options=None, log=None, **kwargs):
        """

        Arguments
        ---------
        nocc_frozen : int
            Number of frozen occupied orbitals. Need to be at the start of mo_coeff.
        nvir_frozen : int
            Number of frozen virtual orbitals. Need to be at the end of mo_coeff.
        """
        self.log = log or fragment.log

        if options is None:
            options = ClusterSolverOptions(**kwargs)
        else:
            options = options.replace(kwargs)
        options = options.replace(self.base.opts, select=NotSet)
        self.opts = options

        self.fragment = fragment
        self.mo_coeff = mo_coeff
        self.mo_occ = mo_occ
        self.nocc_frozen = nocc_frozen
        self.nvir_frozen = nvir_frozen
        # Intermediates
        self._eris = eris
        self._solver = None
        # Output
        self.c1 = None
        self.c2 = None
        self.t1 = None
        self.t2 = None
        self.converged = False
        self.e_corr = 0.0           # Note that this is the full correlation energy
        # Optional output
        self.dm1 = None
        self.ip_energy = None
        self.ip_coeff = None
        self.ea_energy = None
        self.ea_coeff = None

    @property
    def base(self):
        return self.fragment.base

    @property
    def mf(self):
        return self.fragment.mf

    @property
    def nmo(self):
        return self.mo_coeff.shape[-1]

    @property
    def nocc(self):
        return np.count_nonzero(self.mo_occ > 0)

    @property
    def nactive(self):
        return self.nmo - self.nfrozen

    @property
    def nfrozen(self):
        return self.nocc_frozen + self.nvir_frozen

    def get_active_slice(self):
        slc = np.s_[self.nocc_frozen:-self.nvir_frozen]
        return slc

    def get_frozen_indices(self):
        nmo = self.mo_coeff.shape[-1]
        idx = list(range(self.nocc_frozen)) + list(range(nmo-self.nvir_frozen, nmo))
        return idx

    @property
    def c_active_occ(self):
        """Active occupied orbital coefficients."""
        return self.mo_coeff[:,self.nocc_frozen:self.nocc]

    @property
    def c_active_vir(self):
        """Active virtual orbital coefficients."""
        return self.mo_coeff[:,self.nocc:-self.nvir_frozen]

    #def kernel(self, init_guess=None, options=None):

    #    options = options or self.options

    #    if self.solver is None:
    #        pass
    #    elif self.solver == "MP2":
    #        self.run_mp2()
    #    elif self.solver in ("CCSD", "CCSD(T)"):
    #        self.run_ccsd(init_guess=init_guess, options=options)
    #    elif self.solver == "CISD":
    #        # Currently not maintained
    #        self.run_cisd()
    #    elif self.solver in ("FCI-spin0", "FCI-spin1"):
    #        raise NotImplementedError()
    #        self.run_fci()
    #    else:
    #        raise ValueError("Unknown solver: %s" % self.solver)

    #    if self.solver in ("CCSD", "CCSD(T)"):
    #        self.print_t_diagnostic()

    #    log.debug("E(full corr)= %16.8g Ha", self.e_corr)

    #def run_mp2(self):
    #    if self.base.has_pbc:
    #        import pyscf.pbc.mp
    #        cls = pyscf.pbc.mp.MP2
    #    else:
    #        import pyscf.mp
    #        cls = pyscf.mp.MP2
    #    mp2 = cls(self.mf, mo_coeff=self.mo_coeff, mo_occ=self.mo_occ, frozen=self.get_frozen_indices())
    #    self._solver = mp2

    #    if self._eris is None:
    #        t0 = timer()
    #        self._eris = self.base.get_eris(mp2)
    #        log.timing("Time for AO->MO:  %s", time_string(timer()-t0))

    #    self.e_corr, self.c2 = mp2.kernel(eris=self._eris, hf_reference=True)
    #    self.converged = True

    #def run_cisd(self):
    #    # NOT MAINTAINED!!!
    #    import pyscf.ci
    #    import pyscf.pbc.ci

    #    cls = pyscf.pbc.ci.CISD if self.base.has_pbc else pyscf.ci.CISD
    #    ci = cls(self.mf, mo_coeff=self.mo_coeff, mo_occ=self.mo_occ, frozen=self.get_frozen_indices())
    #    self._solver = ci

    #    # Integral transformation
    #    t0 = timer()
    #    eris = ci.ao2mo()
    #    self._eris = eris
    #    log.timing("Time for AO->MO:  %s", time_string(timer()-t0))

    #    t0 = timer()
    #    log.info("Running CISD...")
    #    ci.kernel(eris=eris)
    #    log.info("CISD done. converged: %r", ci.converged)
    #    log.timing("Time for CISD [s]: %.3f (%s)", time_string(timer()-t0))

    #    self.converged = ci.converged
    #    self.e_corr = ci.e_corr

    #    # Renormalize
    #    c0, c1, c2 = pyscf.ci.cisdvec_to_amplitudes(ci.ci)
    #    self.c1 = c1/c0
    #    self.c2 = c2/c0

class CCSDSolver(ClusterSolver):

    def kernel(self, init_guess=None):

        # Do not use pbc.ccsd for Gamma point CCSD -> always use molecular code
        #if self.base.boundary_cond == 'open':
        #    cls = pyscf.cc.CCSD
        #else:
        #    import pyscf.pbc.cc
        #    cls = pyscf.pbc.cc.CCSD
        #    #cls = pyscf.cc.ccsd.CCSD
        cls = pyscf.cc.ccsd.CCSD
        self.log.debug("CCSD class= %r" % cls)
        cc = cls(self.mf, mo_coeff=self.mo_coeff, mo_occ=self.mo_occ, frozen=self.get_frozen_indices())
        self._solver = cc

        # Integral transformation
        if self._eris is None:
            t0 = timer()
            self._eris = self.base.get_eris(cc)
            self.log.timing("Time for AO->MO of (ij|kl):  %s", time_string(timer()-t0))
        #else:
        #    # DEBUG:
        #    eris = self.base.get_eris(cc)
        #    for kind in ["oooo", "ovoo", "ovvo", "oovv", "ovov", "ovvv", "vvvv"]:
        #        diff = getattr(self._eris, kind) - getattr(eris, kind)
        #        log.debug("Difference (%2s|%2s): max= %.2e norm= %.2e", kind[:2], kind[2:], abs(diff).max(), np.linalg.norm(diff))

        t0 = timer()
        if init_guess:
            self.log.info("Running CCSD with initial guess for %r..." % list(init_guess.keys()))
            cc.kernel(eris=self._eris, **init_guess)
        else:
            self.log.info("Running CCSD...")
            cc.kernel(eris=self._eris)
        #log.log((logging.INFO if cc.converged else logging.ERROR), "CCSD done. converged: %r", cc.converged)
        (self.log.info if cc.converged else self.log.error)("CCSD done. converged: %r", cc.converged)
        self.log.debug("E(full corr)= % 16.8f Ha", cc.e_corr)
        self.log.timing("Time for CCSD:  %s", time_string(timer()-t0))

        self.converged = cc.converged
        self.e_corr = cc.e_corr
        self.t1 = cc.t1
        self.t2 = cc.t2
        self.c1 = cc.t1
        self.c2 = cc.t2 + einsum("ia,jb->ijab", cc.t1, cc.t1)

        if self.opts.make_rdm1:
            try:
                t0 = timer()
                self.log.info("Making RDM1...")
                self.dm1 = cc.make_rdm1(eris=self._eris, ao_repr=True)
                self.log.info("RDM1 done. Lambda converged: %r", cc.converged_lambda)
                if not cc.converged_lambda:
                    self.log.warning("Solution of lambda equation not converged!")
                self.log.timing("Time for RDM1:  %s", time_string(timer()-t0))
            except Exception as e:
                self.log.error("Exception while making RDM1: %s", e)

        def eom_ccsd(kind, nroots=3):
            kind = kind.upper()
            assert kind in ("IP", "EA")
            self.log.info("Running %s-EOM-CCSD (nroots=%d)...", kind, nroots)
            eom_funcs = {"IP" : cc.ipccsd , "EA" : cc.eaccsd}
            t0 = timer()
            e, c = eom_funcs[kind](nroots=nroots, eris=self._eris)
            self.log.timing("Time for %s-EOM-CCSD:  %s", kind, time_string(timer()-t0))
            if nroots == 1:
                e, c = [e], [c]
            return e, c

        if self.opts.eom_ccsd in (True, "IP"):
            self.ip_energy, self.ip_coeff = eom_ccsd("IP")
        if self.opts.eom_ccsd in (True, "EA"):
            self.ea_energy, self.ea_coeff = eom_ccsd("EA")

        self.print_t_diagnostic()


    def make_tailor_function(self):
        """Build tailor function.

        This assumes orthogonal fragment spaces.
        """
        ovlp = self.base.get_ovlp()
        c_occ = self.c_active_occ
        c_vir = self.c_active_vir

        def tailor_func(t1, t2):
            tt1 = t1.copy()
            tt2 = t2.copy()
            for fx in self.tailor_fragments:
                sx = fx.cluster_solver
                cx_occ = sx.c_active_occ
                cx_vir = sx.c_active_occ
                # Projections from fragment x occ/vir space to current fragment occ/vir space
                p_occ = np.linalg.multi_dot((cx_occ.T, ovlp, c_occ))
                p_vir = np.linalg.multi_dot((cx_vir.T, ovlp, c_vir))
                # Transform fragment x T-amplitudes
                tx1 = helper.transform_amplitude(sx.t1, p_occ, p_vir)
                tx2 = helper.transform_amplitude(sx.t2, p_occ, p_vir)
                # Form difference with current amplitudes
                # TODO: It shouldn't matter if we use t1/t2 or tt1/tt2 for more than 2 fragments ... check this
                dt1 = (tx1 - t1)
                dt2 = (tx2 - t2)
                # Project onto x's fragment space
                px = fx.get_fragment_projector(c_occ)
                px1 = np.dot(px, td1)
                # OR:
                #px2 = einsum('xi,ijab->xjab', px, dt2) + symmetrize!
                px2 = einsum('xi,yj,ijab->xyab', px, px, dt2)
                # Add contributions
                assert px1.shape == tt1.shape
                assert px2.shape == tt2.shape
                tt1 += px1
                tt2 += px2

            return tt1, tt2

        return tailor_func


    def print_t_diagnostic(self):
        self.log.info("Diagnostic")
        self.log.info("**********")
        try:
            dg_t1 = self._solver.get_t1_diagnostic()
            dg_d1 = self._solver.get_d1_diagnostic()
            dg_d2 = self._solver.get_d2_diagnostic()
            self.log.info("  (T1<0.02: good / D1<0.02: good, D1<0.05: fair / D2<0.15: good, D2<0.18: fair)")
            self.log.info("  (good: MP2~CCSD~CCSD(T) / fair: use MP2/CCSD with caution)")
            dg_t1_msg = "good" if dg_t1 <= 0.02 else "inadequate!"
            dg_d1_msg = "good" if dg_d1 <= 0.02 else ("fair" if dg_d1 <= 0.05 else "inadequate!")
            dg_d2_msg = "good" if dg_d2 <= 0.15 else ("fair" if dg_d2 <= 0.18 else "inadequate!")
            fmtstr = "  * %2s= %6g (%s)"
            self.log.info(fmtstr, "T1", dg_t1, dg_t1_msg)
            self.log.info(fmtstr, "D1", dg_d1, dg_d1_msg)
            self.log.info(fmtstr, "D2", dg_d2, dg_d2_msg)
            if dg_t1 > 0.02 or dg_d1 > 0.05 or dg_d2 > 0.18:
                self.log.warning("  some diagnostic(s) indicate CCSD may not be adequate.")
        except Exception as e:
            self.log.error("Exception in T-diagnostic: %s", e)


class FCISolver(ClusterSolver):
    """Not tested"""


    def kernel(self, init_guess=None):
        import pyscf.mcscf
        import pyscf.ci

        nelectron = sum(self.mo_occ[self.get_active_slice()])
        casci = pyscf.mcscf.CASCI(self.mf, self.nactive, nelectron)
        casci.canonicalization = False

        e_tot, e_cas, wf, *_ = casci.kernel(mo_coeff=self.mo_coeff)
        self.log.debug("FCI done. converged: %r", casci.converged)

        cisdvec = pyscf.ci.cisd.from_fcivec(wf, self.nactive, nelectron)
        nocc_active = nelectron // 2
        c0, c1, c2 = pyscf.ci.cisd.cisdvec_to_amplitudes(cisdvec, self.nactive, nocc_active)
        # Intermediate normalization
        self.log.debug("Weight of reference determinant= %.8e", c0)
        c1 /= c0
        c2 /= c0
        self.c1 = c1
        self.c2 = c2

        self.converged = casci.converged
        self.e_corr = (e_tot - self.mf.e_tot)
        self.log.debug("E(full corr)= % 16.8f Ha", self.e_corr)

        ## Create fake CISD object
        #cisd = pyscf.ci.CISD(self.mf, mo_coeff=self.mo_coeff, mo_occ=self.mo_occ, frozen=self.get_frozen_indices())

        ## Get eris somewhere else?
        #t0 = timer()
        #eris = cisd.ao2mo()
        #self.log.debug("Time for integral transformation: %s", time_string(timer()-t0))


    #def run_fci(self):
    #    nocc_active = len(self.active_occ)
    #    casci = pyscf.mcscf.CASCI(self.mf, self.nactive, 2*nocc_active)
    #    solverobj = casci
    #    # Solver options
    #    casci.verbose = 10
    #    casci.canonicalization = False
    #    #casci.fix_spin_(ss=0)
    #    # TEST SPIN
    #    if solver == "FCI-spin0":
    #        casci.fcisolver = pyscf.fci.direct_spin0.FCISolver(self.mol)
    #    casci.fcisolver.conv_tol = 1e-9
    #    casci.fcisolver.threads = 1
    #    casci.fcisolver.max_cycle = 400
    #    #casci.fcisolver.level_shift = 5e-3

    #    if solver_options:
    #        spin = solver_options.pop("fix_spin", None)
    #        if spin is not None:
    #            self.log.debug("Setting fix_spin to %r", spin)
    #            casci.fix_spin_(ss=spin)

    #        for key, value in solver_options.items():
    #            self.log.debug("Setting solver attribute %s to value %r", key, value)
    #            setattr(casci.fcisolver, key, value)

    #    # The sorting of the orbitals above should already have placed the CAS in the correct position

    #    self.log.debug("Running FCI...")
    #    if self.nelectron_target is None:
    #        e_tot, e_cas, wf, *_ = casci.kernel(mo_coeff=mo_coeff)
    #    # Chemical potential loop
    #    else:

    #        S = self.mf.get_ovlp()
    #        px = self.get_local_projector(mo_coeff)
    #        b = np.linalg.multi_dot((S, self.C_local, self.C_local.T, S))

    #        t = np.linalg.multi_dot((S, mo_coeff, px))
    #        h1e = casci.get_hcore()
    #        h1e_func = casci.get_hcore

    #        cptmin = -4
    #        cptmax = 0
    #        #cptmin = -0.5
    #        #cptmax = +0.5

    #        ntol = 1e-6
    #        e_tot = None
    #        wf = None

    #        def electron_error(chempot):
    #            nonlocal e_tot, wf

    #            #casci.get_hcore = lambda *args : h1e - chempot*b
    #            casci.get_hcore = lambda *args : h1e - chempot*(S-b)

    #            e_tot, e_cas, wf, *_ = casci.kernel(mo_coeff=mo_coeff, ci0=wf)
    #            #e_tot, e_cas, wf, *_ = casci.kernel(mo_coeff=mo_coeff)
    #            dm1xx = np.linalg.multi_dot((t.T, casci.make_rdm1(), t))
    #            nx = np.trace(dm1xx)
    #            nerr = (nx - self.nelectron_target)
    #            self.log.debug("chempot=%16.8g, electrons=%16.8g, error=%16.8g", chempot, nx, nerr)
    #            assert casci.converged

    #            if abs(nerr) < ntol:
    #                self.log.debug("Electron error |%e| below tolerance of %e", nerr, ntol)
    #                raise StopIteration

    #            return nerr

    #        try:
    #            scipy.optimize.brentq(electron_error, cptmin, cptmax)
    #        except StopIteration:
    #            pass

    #        # Reset hcore Hamiltonian
    #        casci.get_hcore = h1e_func

    #    #assert np.allclose(mo_coeff_casci, mo_coeff)
    #    #dma, dmb = casci.make_rdm1s()
    #    #self.log.debug("Alpha: %r", np.diag(dma))
    #    #self.log.debug("Beta: %r", np.diag(dmb))
    #    self.log.debug("FCI done. converged: %r", casci.converged)
    #    #self.log.debug("Shape of WF: %r", list(wf.shape))
    #    cisdvec = pyscf.ci.cisd.from_fcivec(wf, self.nactive, 2*nocc_active)
    #    C0, C1, C2 = pyscf.ci.cisd.cisdvec_to_amplitudes(cisdvec, self.nactive, nocc_active)
    #    # Intermediate normalization
    #    self.log.debug("Weight of reference determinant = %.8e", C0)
    #    renorm = 1/C0
    #    C1 *= renorm
    #    C2 *= renorm

    #    converged = casci.converged
    #    e_corr_full = self.energy_factor*(e_tot - self.mf.e_tot)

    #    # Create fake CISD object
    #    cisd = pyscf.ci.CISD(self.mf, mo_coeff=mo_coeff, mo_occ=mo_occ, frozen=frozen)

    #    if eris is None:
    #        t0 = MPI.Wtime()
    #        eris = cisd.ao2mo()
    #        self.log.debug("Time for integral transformation: %s", time_string(MPI.Wtime()-t0))

    #    pC1, pC2 = self.get_local_amplitudes(cisd, C1, C2)
    #    e_corr = self.get_local_energy(cisd, pC1, pC2, eris=eris)




