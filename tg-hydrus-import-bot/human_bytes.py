"""
Преобразование числа байтов в человекопонятное представление
https://stackoverflow.com/a/63839503
"""

class HumanBytes:
    """Предполагается использовать единственную функцию format
    Константы позволяют модифицировать класс через создание дочерних
    """
    __slots__ = ()  # Запрещаем создание атрибутов экземпляра

    METRIC_LABELS: tuple[str, ...] = ("B", "kB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB", "RB", "QB")
    BINARY_LABELS: tuple[str, ...] = ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB", "YiB", "RiB", "QiB")
    PRECISION_OFFSETS: tuple[float, ...] = (0.5, 0.05, 0.005, 0.0005) # PREDEFINED FOR SPEED.
    PRECISION_FORMATS: tuple[str, ...] = (
        "{}{:.0f} {}",
        "{}{:.1f} {}",
        "{}{:.2f} {}",
        "{}{:.3f} {}"
    ) # PREDEFINED FOR SPEED.

    @staticmethod
    def format(num: int|float, metric: bool=False, precision: int=1) -> str:
        """
        Human-readable formatting of bytes, using binary (powers of 1024)
        or metric (powers of 1000) representation.
        """

        # assert, ранее бывший здесь, отключается флагом -O в Python, что может привести к ошибкам
        if not isinstance(num, (int, float)):
            raise TypeError("num must be an int or float")
        if not isinstance(metric, bool):
            raise TypeError("metric must be a bool")
        if not isinstance(precision, int) or precision < 0:
            raise ValueError("precision must be a non-negative integer")

        unit_labels = HumanBytes.METRIC_LABELS if metric else HumanBytes.BINARY_LABELS
        max_index = len(unit_labels) - 1
        unit_step = 1000 if metric else 1024

        # VERY IMPORTANT:
        # Only accepts the CURRENT unit if we're BELOW the threshold where
        # float rounding behavior would place us into the NEXT unit: F.ex.
        # when rounding a float to 1 decimal, any number ">= 1023.95" will
        # be rounded to "1024.0". Obviously we don't want ugly output such
        # as "1024.0 KiB", since the proper term for that is "1.0 MiB".
        if precision > 3:
            precision_format = "{}{:.%df} {}" % precision
            unit_step_thresh = unit_step - 0.5 / (10 ** precision)
        else:
            precision_format = HumanBytes.PRECISION_FORMATS[precision]
            unit_step_thresh = unit_step - HumanBytes.PRECISION_OFFSETS[precision]

        sign = "-" if num < 0 else ""
        if sign: # Faster than ternary assignment or always running abs().
            num = -num

        unit_index = 0
        while unit_index < max_index and num >= unit_step_thresh:
            # We only shrink the number if we HAVEN'T reached the last unit.
            # NOTE: These looped divisions accumulate floating point rounding
            # errors, but each new division pushes the rounding errors further
            # and further down in the decimals, so it doesn't matter at all.
            num /= unit_step
            unit_index += 1

        return precision_format.format(sign, num, unit_labels[unit_index])
