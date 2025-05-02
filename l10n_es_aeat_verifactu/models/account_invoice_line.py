# -*- coding: utf-8 -*-
##############################################################################
import itertools
from lxml import etree
from openerp import models, fields, api, _, SUPERUSER_ID, exceptions
from openerp.exceptions import except_orm, Warning, RedirectWarning, ValidationError
from openerp.tools import float_compare, ustr, float_round, float_compare
import openerp.addons.decimal_precision as dp
from hashlib import sha256
from json import dumps
from openerp.modules.registry import Registry
import logging
import json
import pytz
from requests import Session
_logger = logging.getLogger(__name__)
from datetime import datetime
try:
   from zeep import Client, Settings
   from zeep.plugins import HistoryPlugin
   from zeep.transports import Transport
except (ImportError, IOError) as err:
    _logger.debug(err)
from urlparse import urlparse #urlencode
from base64 import b64encode, b64decode
import qrcode
from cStringIO import StringIO

class AccountInvoiceLine(models.Model):
    _inherit = 'account.invoice.line'

    @api.multi
    def _get_verifactu_line_price_unit(self):
        """Obtain the effective invoice line price after discount. This is
        obtain through this method, as it can be inherited in other modules
        for altering the expected amount according other criteria."""
        self.ensure_one()
        price_unit = self.price_unit * (1 - (self.discount or 0.0) / 100.0)
        if self.invoice_id.currency_id != \
                self.invoice_id.company_id.currency_id:
            from_currency = self.invoice_id.currency_id. \
                with_context(date=self.invoice_id.date_invoice)
            price_unit = from_currency. \
                compute(price_unit, self.invoice_id.company_id.currency_id,
                        round=False)
        return price_unit

    @api.multi
    def _get_verifactu_line_price_subtotal(self):
        """Obtain the effective invoice line price after discount. Needed as
        we can modify the unit price via inheritance."""
        self.ensure_one()
        price = self._get_verifactu_line_price_unit()
        taxes = self.invoice_line_tax_id.compute_all(
            price, self.quantity, product=self.product_id,
            partner=self.invoice_id.partner_id)
        return taxes['total']

    @api.multi
    def _get_verifactu_tax_line_req(self):
        """Get any possible tax amounts for 'Recargo equivalencia'."""
        self.ensure_one()
        taxes_re = self.invoice_id._get_verifactu_taxes_map(['RE'])
        for tax in self.invoice_line_tax_id:
            if tax in taxes_re:
                price = self._get_verifactu_line_price_unit()
                taxes = tax.compute_all(
                    price, self.quantity, self.product_id,
                    self.invoice_id.partner_id,
                )
                taxes['percentage'] = tax.amount
                return taxes
        return {}

    @api.model
    def _update_verifactu_tax_line(self, tax_dict, tax_line):
        """Update the VERIFACTU taxes dictionary for the passed tax line.

        :param self: Single invoice line record.
        :param tax_dict: Previous VERIFACTU taxes dictionary.
        :param tax_line: Tax line that is being analyzed.
        """
        self.ensure_one()
        if tax_line.child_depend:
            tax_type = abs(tax_line.child_ids.filtered('amount')[:1].amount)
        else:
            tax_type = abs(tax_line.amount)
        if tax_type not in tax_dict:
            tax_dict[tax_type] = {
                'TipoImpositivo': str(tax_type * 100),
                'BaseImponible': 0,
                'CuotaRepercutida': 0,
                'CuotaSoportada': 0,
            }
        # Recargo de equivalencia
        tax_line_req = self._get_verifactu_tax_line_req()
        if tax_line_req:
            tipo_recargo = tax_line_req['percentage'] * 100
            cuota_recargo = tax_line_req['taxes'][0]['amount']
            tax_dict[tax_type]['TipoRecargoEquivalencia'] = tipo_recargo
            tax_dict[tax_type].setdefault('CuotaRecargoEquivalencia', 0)
            tax_dict[tax_type]['CuotaRecargoEquivalencia'] += cuota_recargo
        # Rest of the taxes
        taxes = tax_line.compute_all(
            self._get_verifactu_line_price_unit(), self.quantity,
            self.product_id, self.invoice_id.partner_id,
        )
        tax_dict[tax_type]['BaseImponible'] += taxes['total']
        if self.invoice_id.type in ['out_invoice', 'out_refund']:
            key = 'CuotaRepercutida'
        else:
            key = 'CuotaSoportada'
        if taxes['total'] >= 0:
            verifactu_included_taxes = [t for t in taxes['taxes']
                                  if t['amount'] >= 0]
        else:
            verifactu_included_taxes = [t for t in taxes['taxes'] if t['amount'] < 0]
        for tax in verifactu_included_taxes:
            tax_dict[tax_type][key] += tax['amount']

