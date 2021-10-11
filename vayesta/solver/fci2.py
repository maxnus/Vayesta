import dataclasses

import numpy as np

import pyscf
import pyscf.ao2mo
import pyscf.ci
import pyscf.mcscf
import pyscf.fci
import pyscf.fci.addons

from vayesta.core.util import *
from .solver2 import ClusterSolver


class FCI_Solver(ClusterSolver):

    @dataclasses.dataclass
    class Options(ClusterSolver.Options):
        threads: int = 1            # Number of threads for multi-threaded FCI
        lindep: float = None        # Linear dependency tolerance. If None, use PySCF default
        conv_tol: float = None      # Convergence tolerance. If None, use PySCF default
        solver_spin: bool = True    # Use direct_spin1 if True, or direct_spin0 otherwise
        #solver_spin: bool = False    # Use direct_spin1 if True, or direct_spin0 otherwise
        fix_spin: float = 0.0       # If set to a number, the given S^2 value will be enforced
        #fix_spin: float = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        solver = self.get_solver()
        self.log.debugv("type(solver)= %r", type(solver))
        # Set options
        if self.opts.threads is not None: solver.threads = self.opts.threads
        if self.opts.conv_tol is not None: solver.conv_tol = self.opts.conv_tol
        if self.opts.lindep is not None: solver.lindep = self.opts.lindep
        if self.opts.fix_spin not in (None, False):
            spin = self.opts.fix_spin
            self.log.debugv("Fixing spin of FCI solver to S^2= %f", spin)
            solver = pyscf.fci.addons.fix_spin_(solver, ss=spin)
        self.solver = solver

        # --- Results
        self.civec = None
        self.c0 = None
        self.c1 = None      # In intermediate normalization!
        self.c2 = None      # In intermediate normalization!

    def get_solver(self):
        if self.opts.solver_spin:
            return pyscf.fci.direct_spin1.FCISolver(self.mol)
        else:
            return pyscf.fci.direct_spin0.FCISolver(self.mol)

    @property
    def ncas(self):
        return self.cluster.norb_active

    @property
    def nelec(self):
        return 2*self.cluster.nocc_active

    def get_init_guess(self):
        return {'ci0' : self.civec}

    def get_c1(self):
        return self.c1

    def get_c2(self):
        return self.c2

    def get_eris(self):
        with log_time(self.log.timing, "Time for AO->MO of ERIs:  %s"):
            eris = self.base.get_eris_array(self.cluster.c_active)
            #self.base.debug_eris = eris
        return eris

    def get_heff(self, eris, with_vext=True):
        #nocc = self.nocc - self.nocc_frozen
        f_act = dot(self.cluster.c_active.T, self.base.get_fock(), self.cluster.c_active)
        occ = np.s_[:self.cluster.nocc_active]
        v_act = 2*einsum('iipq->pq', eris[occ,occ]) - einsum('iqpi->pq', eris[occ,:,:,occ])
        h_eff = f_act - v_act
        # This should be equivalent to:
        #core = np.s_[:self.nocc_frozen]
        #dm_core = 2*np.dot(self.mo_coeff[:,core], self.mo_coeff[:,core].T)
        #v_core = self.mf.get_veff(dm=dm_core)
        #h_eff = np.linalg.multi_dot((self.c_active.T, self.base.get_hcore()+v_core, self.c_active))
        if with_vext and self.opts.v_ext is not None:
            h_eff += self.opts.v_ext
        return h_eff

    def kernel(self, ci0=None, eris=None):
        """Run FCI kernel."""

        if eris is None: eris = self.get_eris()
        heff = self.get_heff(eris)

        t0 = timer()
        #self.solver.verbose = 10
        e_fci, self.civec = self.solver.kernel(heff, eris, self.ncas, self.nelec, ci0=ci0)
        if not self.solver.converged:
            self.log.error("FCI not converged!")
        else:
            self.log.debugv("FCI converged.")
        self.log.timing("Time for FCI: %s", time_string(timer()-t0))
        self.log.debugv("E(CAS)= %s", energy_string(e_fci))
        # TODO: This requires the E_core energy (and nuc-nuc repulsion)
        self.e_corr = np.nan
        self.converged = self.solver.converged
        s2, mult = self.solver.spin_square(self.civec, self.ncas, self.nelec)
        self.log.info("FCI: S^2= %.10f  multiplicity= %.10f", s2, mult)
        self.c0, self.c1, self.c2 = self.get_cisd_amps(self.civec)

    #def get_cisd_amps(self, civec):
    #    cisdvec = pyscf.ci.cisd.from_fcivec(civec, self.ncas, self.nelec)
    #    c0, c1, c2 = pyscf.ci.cisd.cisdvec_to_amplitudes(cisdvec, self.ncas, self.cluster.nocc_active)
    #    c1 = c1/c0
    #    c2 = c2/c0
    #    return c0, c1, c2

    def get_cisd_amps(self, civec):
        nocc, nvir = self.cluster.nocc_active, self.cluster.nvir_active
        t1addr, t1sign = pyscf.ci.cisd.t1strs(self.ncas, nocc)
        c0 = civec[0,0]
        c1 = civec[0,t1addr] * t1sign
        c2 = einsum('i,j,ij->ij', t1sign, t1sign, civec[t1addr[:,None],t1addr])
        c1 = c1.reshape(nocc,nvir)
        c2 = c2.reshape(nocc,nvir,nocc,nvir).transpose(0,2,1,3)
        c1 = c1/c0
        c2 = c2/c0
        return c0, c1, c2

    def make_rdm1(self, civec=None):
        if civec is None: civec = self.civec
        self.dm1 = self.solver.make_rdm1(civec, self.ncas, self.nelec)
        return self.dm1

    def make_rdm12(self, civec=None):
        if civec is None: civec = self.civec
        self.dm1, self.dm2 = self.solver.make_rdm12(civec, self.ncas, self.nelec)
        return self.dm1, self.dm2

    def make_rdm2(self, civec=None):
        return self.make_rdm12(civec=civec)[1]

    #def kernel_casci(self, init_guess=None, eris=None):
    #    """Old kernel function, using an CASCI object."""
    #    nelec = sum(self.mo_occ[self.get_active_slice()])
    #    casci = pyscf.mcscf.CASCI(self.mf, self.nactive, nelec)
    #    casci.canonicalization = False
    #    if self.opts.threads is not None: casci.fcisolver.threads = self.opts.threads
    #    if self.opts.conv_tol is not None: casci.fcisolver.conv_tol = self.opts.conv_tol
    #    if self.opts.lindep is not None: casci.fcisolver.lindep = self.opts.lindep
    #    # FCI default values:
    #    #casci.fcisolver.conv_tol = 1e-10
    #    #casci.fcisolver.lindep = 1e-14

    #    self.log.debug("Running CASCI with (%d, %d) CAS", nelec, self.nactive)
    #    t0 = timer()
    #    e_tot, e_cas, wf, *_ = casci.kernel(mo_coeff=self.mo_coeff)
    #    self.log.debug("FCI done. converged: %r", casci.converged)
    #    self.log.timing("Time for FCI: %s", time_string(timer()-t0))
    #    e_corr = (e_tot-self.mf.e_tot)

    #    cisdvec = pyscf.ci.cisd.from_fcivec(wf, self.nactive, nelec)
    #    nocc = nelec // 2
    #    c0, c1, c2 = pyscf.ci.cisd.cisdvec_to_amplitudes(cisdvec, self.nactive, nocc)

    #    # Temporary workaround (eris needed for energy later)
    #    if self.mf._eri is not None:
    #        class ERIs:
    #            pass
    #        eris = ERIs()
    #        c_act = self.mo_coeff[:,self.get_active_slice()]
    #        eris.fock = np.linalg.multi_dot((c_act.T, self.base.get_fock(), c_act))
    #        g = pyscf.ao2mo.full(self.mf._eri, c_act)
    #        o = np.s_[:nocc]
    #        v = np.s_[nocc:]
    #        eris.ovvo = pyscf.ao2mo.restore(1, g, self.nactive)[o,v,v,o]
    #    else:
    #        # TODO
    #        pass

    #    results = self.Results(
    #            converged=casci.converged, e_corr=e_corr,
    #            c_occ=self.cluster.c_active_occ, c_vir=self.cluster.c_active_vir, eris=eris,
    #            c0=c0, c1=c1, c2=c2)

    #    if self.opts.make_rdm2:
    #        results.dm1, results.dm2 = casci.fcisolver.make_rdm12(wf, self.nactive, nelec)
    #    elif self.opts.make_rdm1:
    #        results.dm1 = casci.fcisolver.make_rdm1(wf, self.nactive, nelec)

    #    return results

    #kernel = kernel_casci
