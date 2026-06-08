//===-- Searcher.cpp ------------------------------------------------------===//
//
//                     The KLEE Symbolic Virtual Machine
//
// This file is distributed under the University of Illinois Open Source
// License. See LICENSE.TXT for details.
//
//===----------------------------------------------------------------------===//

#include "Searcher.h"

#include "CoreStats.h"
#include "ExecutionState.h"
#include "ExecutionTree.h"
#include "Executor.h"
#include "MergeHandler.h"
#include "StatsTracker.h"

#include "klee/ADT/DiscretePDF.h"
#include "klee/ADT/RNG.h"
#include "klee/Statistics/Statistics.h"
#include "klee/Module/InstructionInfoTable.h"
#include "klee/Module/KInstruction.h"
#include "klee/Module/KModule.h"
#include "klee/Support/ErrorHandling.h"
#include "klee/System/Time.h"

#include "klee/Support/CompilerWarning.h"
DISABLE_WARNING_PUSH
DISABLE_WARNING_DEPRECATED_DECLARATIONS
#include "llvm/IR/Constants.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/Module.h"
#include "llvm/Support/CommandLine.h"
DISABLE_WARNING_POP

#include <cassert>
#include <cmath>

using namespace klee;
using namespace llvm;


///

ExecutionState &DFSSearcher::selectState() {
  return *states.back();
}

void DFSSearcher::update(ExecutionState *current,
                         const std::vector<ExecutionState *> &addedStates,
                         const std::vector<ExecutionState *> &removedStates) {
  // insert states
  states.insert(states.end(), addedStates.begin(), addedStates.end());

  // remove states
  for (const auto state : removedStates) {
    if (state == states.back()) {
      states.pop_back();
    } else {
      auto it = std::find(states.begin(), states.end(), state);
      assert(it != states.end() && "invalid state removed");
      states.erase(it);
    }
  }
}

bool DFSSearcher::empty() {
  return states.empty();
}

void DFSSearcher::printName(llvm::raw_ostream &os) {
  os << "DFSSearcher\n";
}


///

ExecutionState &BFSSearcher::selectState() {
  return *states.front();
}

void BFSSearcher::update(ExecutionState *current,
                         const std::vector<ExecutionState *> &addedStates,
                         const std::vector<ExecutionState *> &removedStates) {
  // update current state
  // Assumption: If new states were added KLEE forked, therefore states evolved.
  // constraints were added to the current state, it evolved.
  if (!addedStates.empty() && current &&
      std::find(removedStates.begin(), removedStates.end(), current) == removedStates.end()) {
    auto pos = std::find(states.begin(), states.end(), current);
    assert(pos != states.end());
    states.erase(pos);
    states.push_back(current);
  }

  // insert states
  states.insert(states.end(), addedStates.begin(), addedStates.end());

  // remove states
  for (const auto state : removedStates) {
    if (state == states.front()) {
      states.pop_front();
    } else {
      auto it = std::find(states.begin(), states.end(), state);
      assert(it != states.end() && "invalid state removed");
      states.erase(it);
    }
  }
}

bool BFSSearcher::empty() {
  return states.empty();
}

void BFSSearcher::printName(llvm::raw_ostream &os) {
  os << "BFSSearcher\n";
}


///

RandomSearcher::RandomSearcher(RNG &rng) : theRNG{rng} {}

ExecutionState &RandomSearcher::selectState() {
  return *states[theRNG.getInt32() % states.size()];
}

void RandomSearcher::update(ExecutionState *current,
                            const std::vector<ExecutionState *> &addedStates,
                            const std::vector<ExecutionState *> &removedStates) {
  // insert states
  states.insert(states.end(), addedStates.begin(), addedStates.end());

  // remove states
  for (const auto state : removedStates) {
    auto it = std::find(states.begin(), states.end(), state);
    assert(it != states.end() && "invalid state removed");
    states.erase(it);
  }
}

bool RandomSearcher::empty() {
  return states.empty();
}

void RandomSearcher::printName(llvm::raw_ostream &os) {
  os << "RandomSearcher\n";
}


///

WeightedRandomSearcher::WeightedRandomSearcher(WeightType type, RNG &rng)
  : states(std::make_unique<DiscretePDF<ExecutionState*, ExecutionStateIDCompare>>()),
    theRNG{rng},
    type(type) {

  switch(type) {
  case Depth:
  case RP:
    updateWeights = false;
    break;
  case InstCount:
  case CPInstCount:
  case QueryCost:
  case MinDistToUncovered:
  case CoveringNew:
    updateWeights = true;
    break;
  default:
    assert(0 && "invalid weight type");
  }
}

ExecutionState &WeightedRandomSearcher::selectState() {
  return *states->choose(theRNG.getDoubleL());
}

double WeightedRandomSearcher::getWeight(ExecutionState *es) {
  switch(type) {
    default:
    case Depth:
      return es->depth;
    case RP:
      return std::pow(0.5, es->depth);
    case InstCount: {
      uint64_t count = theStatisticManager->getIndexedValue(stats::instructions,
                                                            es->pc->info->id);
      double inv = 1. / std::max((uint64_t) 1, count);
      return inv * inv;
    }
    case CPInstCount: {
      StackFrame &sf = es->stack.back();
      uint64_t count = sf.callPathNode->statistics.getValue(stats::instructions);
      double inv = 1. / std::max((uint64_t) 1, count);
      return inv;
    }
    case QueryCost:
      return (es->queryMetaData.queryCost.toSeconds() < .1)
                 ? 1.
                 : 1. / es->queryMetaData.queryCost.toSeconds();
    case CoveringNew:
    case MinDistToUncovered: {
      uint64_t md2u = computeMinDistToUncovered(es->pc,
                                                es->stack.back().minDistToUncoveredOnReturn);

      double invMD2U = 1. / (md2u ? md2u : 10000);
      if (type == CoveringNew) {
        double invCovNew = 0.;
        if (es->instsSinceCovNew)
          invCovNew = 1. / std::max(1, (int) es->instsSinceCovNew - 1000);
        return (invCovNew * invCovNew + invMD2U * invMD2U);
      } else {
        return invMD2U * invMD2U;
      }
    }
  }
}

void WeightedRandomSearcher::update(ExecutionState *current,
                                    const std::vector<ExecutionState *> &addedStates,
                                    const std::vector<ExecutionState *> &removedStates) {

  // update current
  if (current && updateWeights &&
      std::find(removedStates.begin(), removedStates.end(), current) == removedStates.end())
    states->update(current, getWeight(current));

  // insert states
  for (const auto state : addedStates)
    states->insert(state, getWeight(state));

  // remove states
  for (const auto state : removedStates)
    states->remove(state);
}

bool WeightedRandomSearcher::empty() {
  return states->empty();
}

void WeightedRandomSearcher::printName(llvm::raw_ostream &os) {
  os << "WeightedRandomSearcher::";
  switch(type) {
    case Depth              : os << "Depth\n"; return;
    case RP                 : os << "RandomPath\n"; return;
    case QueryCost          : os << "QueryCost\n"; return;
    case InstCount          : os << "InstCount\n"; return;
    case CPInstCount        : os << "CPInstCount\n"; return;
    case MinDistToUncovered : os << "MinDistToUncovered\n"; return;
    case CoveringNew        : os << "CoveringNew\n"; return;
    default                 : os << "<unknown type>\n"; return;
  }
}


///

// Check if n is a valid pointer and a node belonging to us
#define IS_OUR_NODE_VALID(n)                                                   \
  (((n).getPointer() != nullptr) && (((n).getInt() & idBitMask) != 0))

RandomPathSearcher::RandomPathSearcher(InMemoryExecutionTree *executionTree, RNG &rng)
    : executionTree{executionTree}, theRNG{rng},
      idBitMask{static_cast<uint8_t>(executionTree ? executionTree->getNextId() : 0)} {
  assert(executionTree);
};

ExecutionState &RandomPathSearcher::selectState() {
  unsigned flips=0, bits=0;
  assert(executionTree->root.getInt() & idBitMask &&
         "Root should belong to the searcher");
  ExecutionTreeNode *n = executionTree->root.getPointer();
  while (!n->state) {
    if (!IS_OUR_NODE_VALID(n->left)) {
      assert(IS_OUR_NODE_VALID(n->right) && "Both left and right nodes invalid");
      assert(n != n->right.getPointer());
      n = n->right.getPointer();
    } else if (!IS_OUR_NODE_VALID(n->right)) {
      assert(IS_OUR_NODE_VALID(n->left) && "Both right and left nodes invalid");
      assert(n != n->left.getPointer());
      n = n->left.getPointer();
    } else {
      if (bits==0) {
        flips = theRNG.getInt32();
        bits = 32;
      }
      --bits;
      n = ((flips & (1U << bits)) ? n->left : n->right).getPointer();
    }
  }

  return *n->state;
}

void RandomPathSearcher::update(ExecutionState *current,
                                const std::vector<ExecutionState *> &addedStates,
                                const std::vector<ExecutionState *> &removedStates) {
  // insert states
  for (auto es : addedStates) {
    ExecutionTreeNode *etnode = es->executionTreeNode, *parent = etnode->parent;
    ExecutionTreeNodePtr *childPtr;

    childPtr = parent ? ((parent->left.getPointer() == etnode) ? &parent->left
                                                               : &parent->right)
                      : &executionTree->root;
    while (etnode && !IS_OUR_NODE_VALID(*childPtr)) {
      childPtr->setInt(childPtr->getInt() | idBitMask);
      etnode = parent;
      if (etnode)
        parent = etnode->parent;

      childPtr = parent
                     ? ((parent->left.getPointer() == etnode) ? &parent->left
                                                              : &parent->right)
                     : &executionTree->root;
    }
  }

  // remove states
  for (auto es : removedStates) {
    ExecutionTreeNode *etnode = es->executionTreeNode, *parent = etnode->parent;

    while (etnode && !IS_OUR_NODE_VALID(etnode->left) &&
           !IS_OUR_NODE_VALID(etnode->right)) {
      auto childPtr =
          parent ? ((parent->left.getPointer() == etnode) ? &parent->left
                                                          : &parent->right)
                 : &executionTree->root;
      assert(IS_OUR_NODE_VALID(*childPtr) &&
             "Removing executionTree child not ours");
      childPtr->setInt(childPtr->getInt() & ~idBitMask);
      etnode = parent;
      if (etnode)
        parent = etnode->parent;
    }
  }
}

bool RandomPathSearcher::empty() {
  return !IS_OUR_NODE_VALID(executionTree->root);
}

void RandomPathSearcher::printName(llvm::raw_ostream &os) {
  os << "RandomPathSearcher\n";
}


///

MergingSearcher::MergingSearcher(Searcher *baseSearcher)
  : baseSearcher{baseSearcher} {};

void MergingSearcher::pauseState(ExecutionState &state) {
  assert(std::find(pausedStates.begin(), pausedStates.end(), &state) == pausedStates.end());
  pausedStates.push_back(&state);
  baseSearcher->update(nullptr, {}, {&state});
}

void MergingSearcher::continueState(ExecutionState &state) {
  auto it = std::find(pausedStates.begin(), pausedStates.end(), &state);
  assert(it != pausedStates.end());
  pausedStates.erase(it);
  baseSearcher->update(nullptr, {&state}, {});
}

ExecutionState& MergingSearcher::selectState() {
  assert(!baseSearcher->empty() && "base searcher is empty");

  if (!UseIncompleteMerge)
    return baseSearcher->selectState();

  // Iterate through all MergeHandlers
  for (auto cur_mergehandler: mergeGroups) {
    // Find one that has states that could be released
    if (!cur_mergehandler->hasMergedStates()) {
      continue;
    }
    // Find a state that can be prioritized
    ExecutionState *es = cur_mergehandler->getPrioritizeState();
    if (es) {
      return *es;
    } else {
      if (DebugLogIncompleteMerge){
        llvm::errs() << "Preemptively releasing states\n";
      }
      // If no state can be prioritized, they all exceeded the amount of time we
      // are willing to wait for them. Release the states that already arrived at close_merge.
      cur_mergehandler->releaseStates();
    }
  }
  // If we were not able to prioritize a merging state, just return some state
  return baseSearcher->selectState();
}

void MergingSearcher::update(ExecutionState *current,
                             const std::vector<ExecutionState *> &addedStates,
                             const std::vector<ExecutionState *> &removedStates) {
  // We have to check if the current execution state was just deleted, as to
  // not confuse the nurs searchers
  if (std::find(pausedStates.begin(), pausedStates.end(), current) == pausedStates.end()) {
    baseSearcher->update(current, addedStates, removedStates);
  }
}

bool MergingSearcher::empty() {
  return baseSearcher->empty();
}

void MergingSearcher::printName(llvm::raw_ostream &os) {
  os << "MergingSearcher\n";
}


///

BatchingSearcher::BatchingSearcher(Searcher *baseSearcher,
                                   time::Span timeBudget,
                                   unsigned instructionBudget)
    : baseSearcher{baseSearcher}, timeBudgetEnabled{timeBudget},
      timeBudget{timeBudget}, instructionBudgetEnabled{instructionBudget > 0},
      instructionBudget{instructionBudget} {};

bool BatchingSearcher::withinTimeBudget() const {
  return !timeBudgetEnabled ||
         (time::getWallTime() - lastStartTime) <= timeBudget;
}

bool BatchingSearcher::withinInstructionBudget() const {
  return !instructionBudgetEnabled ||
         (stats::instructions - lastStartInstructions) <= instructionBudget;
}

ExecutionState &BatchingSearcher::selectState() {
  if (lastState && withinTimeBudget() && withinInstructionBudget()) {
    // return same state for as long as possible
    return *lastState;
  }

  // ensure time budget is larger than time between two calls (for same state)
  if (lastState && timeBudgetEnabled) {
    time::Span delta = time::getWallTime() - lastStartTime;
    auto t = timeBudget;
    t *= 1.1;
    if (delta > t) {
      klee_message("increased time budget from %f to %f\n",
                   timeBudget.toSeconds(), delta.toSeconds());
      timeBudget = delta;
    }
  }

  // pick a new state
  lastState = &baseSearcher->selectState();
  if (timeBudgetEnabled) {
    lastStartTime = time::getWallTime();
  }
  if (instructionBudgetEnabled) {
    lastStartInstructions = stats::instructions;
  }
  return *lastState;
}

void BatchingSearcher::update(ExecutionState *current,
                              const std::vector<ExecutionState *> &addedStates,
                              const std::vector<ExecutionState *> &removedStates) {
  // drop memoized state if it is marked for deletion
  if (std::find(removedStates.begin(), removedStates.end(), lastState) != removedStates.end())
    lastState = nullptr;
  // update underlying searcher
  baseSearcher->update(current, addedStates, removedStates);
}

bool BatchingSearcher::empty() {
  return baseSearcher->empty();
}

void BatchingSearcher::printName(llvm::raw_ostream &os) {
  os << "<BatchingSearcher> timeBudget: " << timeBudget
     << ", instructionBudget: " << instructionBudget
     << ", baseSearcher:\n";
  baseSearcher->printName(os);
  os << "</BatchingSearcher>\n";
}


///

IterativeDeepeningTimeSearcher::IterativeDeepeningTimeSearcher(Searcher *baseSearcher)
  : baseSearcher{baseSearcher} {};

ExecutionState &IterativeDeepeningTimeSearcher::selectState() {
  ExecutionState &res = baseSearcher->selectState();
  startTime = time::getWallTime();
  return res;
}

void IterativeDeepeningTimeSearcher::update(ExecutionState *current,
                                            const std::vector<ExecutionState *> &addedStates,
                                            const std::vector<ExecutionState *> &removedStates) {

  const auto elapsed = time::getWallTime() - startTime;

  // update underlying searcher (filter paused states unknown to underlying searcher)
  if (!removedStates.empty()) {
    std::vector<ExecutionState *> alt = removedStates;
    for (const auto state : removedStates) {
      auto it = pausedStates.find(state);
      if (it != pausedStates.end()) {
        pausedStates.erase(it);
        alt.erase(std::remove(alt.begin(), alt.end(), state), alt.end());
      }
    }    
    baseSearcher->update(current, addedStates, alt);
  } else {
    baseSearcher->update(current, addedStates, removedStates);
  }

  // update current: pause if time exceeded
  if (current &&
      std::find(removedStates.begin(), removedStates.end(), current) == removedStates.end() &&
      elapsed > time) {
    pausedStates.insert(current);
    baseSearcher->update(nullptr, {}, {current});
  }

  // no states left in underlying searcher: fill with paused states
  if (baseSearcher->empty()) {
    time *= 2U;
    klee_message("increased time budget to %f\n", time.toSeconds());
    std::vector<ExecutionState *> ps(pausedStates.begin(), pausedStates.end());
    baseSearcher->update(nullptr, ps, std::vector<ExecutionState *>());
    pausedStates.clear();
  }
}

bool IterativeDeepeningTimeSearcher::empty() {
  return baseSearcher->empty() && pausedStates.empty();
}

void IterativeDeepeningTimeSearcher::printName(llvm::raw_ostream &os) {
  os << "IterativeDeepeningTimeSearcher\n";
}


///

bool ParserGuidedSearcher::isInputByteEquality(const ref<Expr> &e) {
  auto *eq = dyn_cast<EqExpr>(e);
  if (!eq)
    return false;

  // Canonical form: constant on the left.
  auto *constSide = dyn_cast<ConstantExpr>(eq->left);
  if (!constSide)
    return false;

  // Eq(false_1bit, X) is a negation (the false-branch constraint), not an
  // equality pinning a byte to a value.
  if (constSide->getWidth() == Expr::Bool && constSide->isFalse())
    return false;

  // The right-hand side must be a ReadExpr from a symbolic array at a
  // constant index — i.e. "input byte N == constant C".
  auto *readSide = dyn_cast<ReadExpr>(eq->right);
  if (!readSide)
    return false;

  if (!readSide->updates.root->isSymbolicArray())
    return false;

  if (!isa<ConstantExpr>(readSide->index))
    return false;

  return true;
}

void ParserGuidedSearcher::addToTier1(ExecutionState *state) {
  ++tier1Classifications;
  if (tier1Set.insert(state).second)
    tier1States.push_back(state);
}

void ParserGuidedSearcher::removeFromTier1(ExecutionState *state) {
  if (tier1Set.erase(state)) {
    auto it = std::find(tier1States.begin(), tier1States.end(), state);
    assert(it != tier1States.end());
    tier1States.erase(it);
  }
}

ParserGuidedSearcher::ParserGuidedSearcher(RNG &rng)
    : tier3Searcher(std::make_unique<WeightedRandomSearcher>(
          WeightedRandomSearcher::CoveringNew, rng)) {}

ParserGuidedSearcher::~ParserGuidedSearcher() {
  uint64_t total = tier1Selections + tier2Selections + tier3Selections;
  klee_message("ParserGuidedSearcher stats: "
               "selections T1=%lu T2=%lu T3=%lu total=%lu | "
               "tier1 classifications=%lu",
               (unsigned long)tier1Selections,
               (unsigned long)tier2Selections,
               (unsigned long)tier3Selections,
               (unsigned long)total,
               (unsigned long)tier1Classifications);
}

ExecutionState &ParserGuidedSearcher::selectState() {
  unsigned startTier = roundRobinIndex % 3;
  roundRobinIndex++;

  for (unsigned i = 0; i < 3; ++i) {
    switch ((startTier + i) % 3) {
    case 0:
      if (!tier1States.empty()) {
        ++tier1Selections;
        return *tier1States.back();
      }
      break;
    case 1:
      if (!tier2States.empty()) {
        ++tier2Selections;
        return *tier2States.front();
      }
      break;
    case 2:
      if (!tier3Searcher->empty()) {
        ++tier3Selections;
        return tier3Searcher->selectState();
      }
      break;
    }
  }

  llvm_unreachable("ParserGuidedSearcher::selectState called when empty");
}

void ParserGuidedSearcher::update(
    ExecutionState *current, const std::vector<ExecutionState *> &addedStates,
    const std::vector<ExecutionState *> &removedStates) {

  // Forward to tier 3 (covnew) — it maintains its own weighted structure.
  tier3Searcher->update(current, addedStates, removedStates);

  // Remove terminated states from tiers 1 and 2.
  for (const auto state : removedStates) {
    removeFromTier1(state);
    if (state == tier2States.front()) {
      tier2States.pop_front();
    } else {
      auto it = std::find(tier2States.begin(), tier2States.end(), state);
      assert(it != tier2States.end() && "invalid state removed");
      tier2States.erase(it);
    }
  }

  // When a fork happened, current was modified in-place (got a new
  // constraint).  Move it to the back of the BFS queue and re-classify.
  if (!addedStates.empty() && current &&
      std::find(removedStates.begin(), removedStates.end(), current) ==
          removedStates.end()) {
    auto pos = std::find(tier2States.begin(), tier2States.end(), current);
    assert(pos != tier2States.end());
    tier2States.erase(pos);
    tier2States.push_back(current);

    // Re-classify: remove from tier 1 first, then maybe re-add.
    removeFromTier1(current);
    if (!current->constraints.empty()) {
      ref<Expr> lastConstraint = *std::prev(current->constraints.end());
      if (isInputByteEquality(lastConstraint))
        addToTier1(current);
    }
  }

  // Add new states to tier 2 (BFS) and classify for tier 1.
  for (const auto state : addedStates) {
    tier2States.push_back(state);
    if (!state->constraints.empty()) {
      ref<Expr> lastConstraint = *std::prev(state->constraints.end());
      if (isInputByteEquality(lastConstraint))
        addToTier1(state);
    }
  }
}

bool ParserGuidedSearcher::empty() {
  return tier2States.empty();
}

void ParserGuidedSearcher::printName(llvm::raw_ostream &os) {
  os << "ParserGuidedSearcher\n";
}


///

InterleavedSearcher::InterleavedSearcher(const std::vector<Searcher*> &_searchers) {
  searchers.reserve(_searchers.size());
  for (auto searcher : _searchers)
    searchers.emplace_back(searcher);
}

ExecutionState &InterleavedSearcher::selectState() {
  Searcher *s = searchers[--index].get();
  if (index == 0) index = searchers.size();
  return s->selectState();
}

void InterleavedSearcher::update(ExecutionState *current,
                                 const std::vector<ExecutionState *> &addedStates,
                                 const std::vector<ExecutionState *> &removedStates) {

  // update underlying searchers
  for (auto &searcher : searchers)
    searcher->update(current, addedStates, removedStates);
}

bool InterleavedSearcher::empty() {
  return searchers[0]->empty();
}

void InterleavedSearcher::printName(llvm::raw_ostream &os) {
  os << "<InterleavedSearcher> containing " << searchers.size() << " searchers:\n";
  for (const auto &searcher : searchers)
    searcher->printName(os);
  os << "</InterleavedSearcher>\n";
}
