from typing import *

if TYPE_CHECKING:
    from numpy.typing import ArrayLike
    ArrayOrArrayTuple = Union[ArrayLike, Tuple[ArrayLike, ...]]
