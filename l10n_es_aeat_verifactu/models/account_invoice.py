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
import psycopg2

#VARIABLES VERIFACTU
####################
VERIFACTU_VERSION = 1.0
VERIFACTU_DATE_FORMAT = "%d-%m-%Y"
VERIFACTU_MACRODATA_LIMIT = 100000000.0
VERIFACTU_STATES = [
    ('not_sent', 'Not sent'),
    ('sent', 'Sent'),
    ('sent_w_errors', 'Accepted with errors'),
    ('sent_modified', 'Registered in SII but last modifications not sent'),
    ('cancelled', 'Cancelled'),
    ('cancelled_modified', 'Cancelled in SII but last modifications not sent'),
]
#VARIABLES HASH FACTURA
#######################
#forbidden fields
INTEGRITY_HASH_MOVE_FIELDS = ('date_invoice', 'journal_id', 'company_id')
INTEGRITY_HASH_LINE_FIELDS = ('price_subtotal', 'price_subtotal', 'account_id', 'partner_id')
VERIFACTU_VALID_INVOICE_STATES = ["open", "paid"]


class account_invoice(models.Model):
    _inherit = ["account.invoice"]

    verifactu_enabled = fields.Boolean(
        string="Enable AEAT",
        compute="_compute_verifactu_enabled",
    )

    verifactu_send_failed = fields.Boolean("Send failed", readonly=True)
    verifactu_send_error = fields.Text("Send error", readonly=True, copy=False)
    verifactu_header_sent = fields.Text(
        string="VERIFACTU last header sent", copy=False, readonly=True,
    )
    verifactu_content_sent = fields.Text(
        string="VERIFACTU last content sent", copy=False, readonly=True,
    )

    verifactu_hash_string = fields.Text("Verifactu HASH String")
    verifactu_hash = fields.Char("Verifactu HASH")
    verifactu_qr_url = fields.Char(string="Verifactu QR URL")
    qr_image = fields.Binary("Image QRL Invoice")
    

    verifactu_refund_type = fields.Selection(
        selection=[
            ('S', 'By substitution'),
            ("I", "By differences"),
        ],
        compute="_compute_verifactu_refund_type",
        store=True,
        readonly=False,
    )
    verifactu_description = fields.Text(
        copy=False, readonly=True
    )
    verifactu_macrodata = fields.Boolean(
        string="MacroData",
        help="Check to confirm that the document has an absolute amount "
        "greater o equal to 100 000 000,00 euros.",
        compute="_compute_verifactu_macrodata",
    )
    verifactu_csv = fields.Char(copy=False, readonly=True)
    verifactu_return = fields.Text(copy=False, readonly=True)
    verifactu_registration_key = fields.Many2one(
        comodel_name="aeat.verifactu.registration.keys",
        compute="_compute_verifactu_registration_key",
        store=True,
        readonly=False,
    )
    verifactu_tax_key = fields.Selection(
        string="Verifactu tax key",
        selection="_get_verifactu_tax_keys",
        compute="_compute_verifactu_tax_key",
        store=True,
        readonly=False,
    )
    verifactu_registration_key_code = fields.Char(
        compute="_compute_verifactu_registration_key_code",
        readonly=True,
        string="Verifactu Code",
    )

    verifactu_refund_specific_invoice_type = fields.Selection(
        selection=[
            (
                "R1",
                _("FACTURA RECTIFICATIVA (Art 80.1 y 80.2 y error fundado en derecho)"),
            ),
            ("R2", _("FACTURA RECTIFICATIVA (Art. 80.3)")),
            ("R3", _("FACTURA RECTIFICATIVA (Art. 80.4)")),
            ("R4", _("FACTURA RECTIFICATIVA (Resto)")),
            ("R5", _("FACTURA RECTIFICATIVA EN FACTURAS SIMPLIFICADAS")),
        ],
        help="Fill this field when the refund are one of the specific cases"
        " of article 80 of LIVA for notifying to Vertifactu with the proper"
        " invoice type.",
    )

    verifactu_state = fields.Selection(
        selection=VERIFACTU_STATES, string="VERIFACTU send state", default='not_sent',
        readonly=True, copy=False,
        help="Indicates the state of this invoice in relation with the "
             "presentation at the SII",
    )

    verifactu_previous_document_id = fields.Reference(
        string="Previous Verifactu Document",
        selection="_selection_verifactu_reference_models",
        readonly=True,
        copy=False,
    )
    verifactu_next_document_id = fields.Reference(
        string="Next Verifactu Document",
        selection="_selection_verifactu_reference_models",
        readonly=True,
        copy=False,
    )
    verifactu_send_date = fields.Datetime(index=True, copy=False)
    verifactu_registration_date = fields.Datetime(copy=False)

    @api.model
    def _selection_verifactu_reference_models(self):
        # this method is used to define the models that can be used as
        # previous documents in the verifactu mixin
        # it can be inherited to add others models if needed like pos.order
        return [("account.invoice", "Invoice")]


    @api.multi
    def invoice_validate(self):
        res = super(account_invoice, self).invoice_validate() #action_number()
        for record in self:
           if record.verifactu_enabled and record.verifactu_state == "not_sent":
                record.verifactu_registration_date = datetime.now()
                #raise Warning(record.verifactu_registration_date)
                record._generate_verifactu_chaining()
                #record._process_verifactu_send()
                #LLAMADA A LA GENERACIÓN DEL HASH Y QR
                #self._compute_verifactu_hash()
                record._compute_verifactu_qr_url()
                #LLAMADA AL ENVIO A VERIFACTU
                #self.send_verifactu()
        return res

    def _generate_verifactu_chaining(self):
        self.ensure_one()
        #self.company_id.flush_recordset(["verifactu_last_document_id"])
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    "SELECT verifactu_last_document_id FROM"
                    " res_company WHERE id = %s FOR UPDATE NOWAIT",
                    [self.company_id.id],
                )
                result = self.env.cr.fetchone()[0]
                prev_doc = False
                if result:
                    document_data = result.split(",")
                    prev_doc = self.env[document_data[0]].browse(int(document_data[1]))
                self.verifactu_previous_document_id = prev_doc
                verifactu_hash_values = self._get_verifactu_hash_string()
                self.verifactu_hash_string = verifactu_hash_values
                hash_string = sha256(verifactu_hash_values.encode("utf-8"))
                #raise Warning(hash_string)
                self.verifactu_hash = hash_string.hexdigest().upper()
                if prev_doc:
                    prev_doc.verifactu_next_document_id = self
                doc_reference = "{model},{id}".format(model=self._name, id=self.id)
                self.env.cr.execute(
                    "UPDATE res_company SET "
                    "verifactu_last_document_id = %s"
                    "WHERE id = %s",
                    [doc_reference, self.company_id.id],
                )
                #self.company_id.invalidate_recordset(["verifactu_last_document_id"])
        except psycopg2.OperationalError as err:
            if err.pgcode == "55P03":  # could not obtain the lock
                raise UserError(_("Could not obtain last document sent to verifactu.")) #from err
            raise

    def _process_verifactu_send(self):
        for record in self:
            record.verifactu_send_date = fields.Datetime.now()
            record.confirm_verifactu_one_document()

    def confirm_verifactu_one_document(self):
        self.sudo()._send_document_to_verifactu()

    @api.depends("type")
    def _compute_verifactu_refund_type(self):
        for record in self:
            if record.type == "out_refund":
                record.verifactu_refund_type = "I"
            else:
                record.verifactu_refund_type = False

    @api.depends("amount_total")
    def _compute_verifactu_macrodata(self):
        return super()._compute_verifactu_macrodata()

    @api.depends(
        "company_id",
        "company_id.verifactu_enabled",
        "type",
        "fiscal_position",
        #"fiscal_position.aeat_active",
    )
    def _compute_verifactu_enabled(self):
        for invoice in self:
            if invoice.company_id.verifactu_enabled: # and invoice.is_invoice():
                invoice.verifactu_enabled = (
                    invoice.fiscal_position
                    #and invoice.fiscal_position.aeat_active
                ) or not invoice.fiscal_position
            else:
                invoice.verifactu_enabled = False

    def _get_verifactu_document_type(self):
        invoice_type = ""
        if self.type in ["out_invoice", "out_refund"]:
            is_simplified = self._is_aeat_simplified_invoice()
            invoice_type = "F2" if is_simplified else "F1"
            if self.type == "out_refund":
                if self.verifactu_refund_specific_invoice_type:
                    invoice_type = self.verifactu_refund_specific_invoice_type
                else:
                    invoice_type = "R5" if is_simplified else "R1"
        return invoice_type

    def _get_verifactu_description(self):
        return self.verifactu_description or self.company_id.verifactu_description

    def _get_document_date(self):
        """
        TODO: this method is the same in l10n_es_aeat_sii_oca, so I think that
        it should be directly in l10n_es_aeat
        """
        return self.date_invoice

    def _aeat_get_partner(self):
        """
        TODO: this method is the same in l10n_es_aeat_sii_oca, so I think that
        it should be directly in l10n_es_aeat
        """
        return self.commercial_partner_id

    def _get_document_fiscal_date(self):
        """
        TODO: this method is the same in l10n_es_aeat_sii_oca, so I think that
        it should be directly in l10n_es_aeat
        """
        return self.date_invoice

    def _get_mapping_key(self):
        """
        TODO: this method is the same in l10n_es_aeat_sii_oca, so I think that
        it should be directly in l10n_es_aeat
        """
        return self.type

    def _get_valid_document_states(self):
        return VERIFACTU_VALID_INVOICE_STATES

    def _get_document_serial_number(self):
        """
        TODO: this method is the same in l10n_es_aeat_sii_oca, so I think that
        it should be directly in l10n_es_aeat
        """
        serial_number = (self.number or "")[0:60]
        #if self.thirdparty_invoice:
        #    serial_number = self.thirdparty_number[0:60]
        return serial_number

    def _get_verifactu_issuer(self):
        return self.company_id.partner_id.vat[2:] #_parse_aeat_vat_info()[2]

    def _get_verifactu_amount_tax(self):
        return self.amount_tax #_signed

    def _get_verifactu_amount_total(self):
        return self.amount_total #_signed

    def _get_verifactu_previous_hash(self):
        if self.verifactu_previous_document_id:
            return self.verifactu_previous_document_id.verifactu_hash
        return ""

    def _get_verifactu_registration_date(self):
        # Date format must be ISO 8601
        #if self.verifactu_registration_date < datetime.now():
        #self.verifactu_registration_date = datetime.now()
        madrid = pytz.timezone('Europe/Madrid')
        create_date = datetime.strptime(self.verifactu_registration_date, '%Y-%m-%d %H:%M:%S')
        #create_date = create_date.replace(tzinfo=pytz.UTC).isoformat()
        #raise Warning(create_date)
        create_date = madrid.localize(create_date)
        iso_date = create_date.isoformat()
        return iso_date
        """return (
            pytz.utc.localize(self.verifactu_registration_date)
            .astimezone()
            .isoformat(timespec="seconds")
         )"""
        """else:
         return (
            pytz.utc.localize(datetime.now())
            .astimezone()
            .isoformat(timespec="seconds")
        )"""

    @api.model
    def _get_verifactu_hash_string(self):
        """Gets the verifactu hash string"""
        if (
            not self.verifactu_enabled
            or self.state == "draft"
            or self.type not in ("out_invoice", "out_refund")
        ):
            return ""
        issuerID = self._get_verifactu_issuer()
        serialNumber = self._get_document_serial_number()
        expeditionDate = self._change_date_format(self._get_document_date())
        documentType = self._get_verifactu_document_type()
        amountTax = self._get_verifactu_amount_tax()
        amountTotal = self._get_verifactu_amount_total()
        previousHash = self._get_verifactu_previous_hash()
        registrationDate = self._get_verifactu_registration_date()
        verifactu_hash_string = (
            "IDEmisorFactura={}&".format(issuerID) +
            "NumSerieFactura={}&".format(serialNumber) +
            "FechaExpedicionFactura={}&".format(expeditionDate) +
            "TipoFactura={}&".format(documentType) +
            "CuotaTotal={}&".format(amountTax) +
            "ImporteTotal={}&".format(amountTotal) +
            "Huella={}&".format(previousHash) +
            "FechaHoraHusoGenRegistro={}".format(registrationDate)
        )
        #raise Warning(verifactu_hash_string)
        return verifactu_hash_string


    @api.model
    def _get_subsanation_verifactu_hash(self):
        verifactu_hash_values = self._get_verifactu_hash_string()
        hash_string = sha256(verifactu_hash_values.encode("utf-8"))
        #raise Warning(hash_string.hexdigest().upper())
        return hash_string.hexdigest().upper()

    @api.model
    def _get_verifactu_invoice_dict_out(self, cancel=False):
        """Build dict with data to send to AEAT WS for document types:
        out_invoice and out_refund.

        :param cancel: It indicates if the dictionary is for sending a
          cancellation of the document.
        :return: documents (dict) : Dict XML with data for this document.
        """
        #self.ensure_one()
        document_date = self._change_date_format(self._get_document_date())
        company = self.company_id
        serial_number = self._get_document_serial_number()
        amount_tax = self._get_verifactu_amount_tax()
        amount_total = self._get_verifactu_amount_total()
        taxes_dict, amount_tax, amount_total = self._get_verifactu_taxes_and_total()
        company_vat = company.partner_id.vat[2:] #_parse_aeat_vat_info()[2]
        verifactu_doc_type = self._get_verifactu_document_type()
        registroAlta = {}
        inv_dict = {
            "IDVersion": self._get_verifactu_version(),
            "IDFactura": {
                "IDEmisorFactura": company_vat,
                "NumSerieFactura": serial_number,
                "FechaExpedicionFactura": document_date,
            },
            "NombreRazonEmisor": self.company_id.name[0:120],
            "TipoFactura": verifactu_doc_type,
        }
        if self.type == "out_refund":
            inv_dict["TipoRectificativa"] = self.verifactu_refund_type
            origin = self.origin_invoices_ids[0]
            if origin:
               if self.verifactu_refund_type == "I":
                inv_dict["FacturasRectificadas"] = []
                origin = origin.number
                if origin:
                    orig_document_date = self._change_date_format(
                        origin._get_document_date()
                    )
                    orig_serial_number = origin._get_document_serial_number()
                    origin_data = {
                        "IDFacturaRectificada": {
                            "IDEmisorFactura": company_vat,
                            "NumSerieFactura": orig_serial_number,
                            "FechaExpedicionFactura": orig_document_date,
                        }
                    }
                    inv_dict["FacturasRectificadas"].append(origin_data)
               inv_dict["ImporteRectificacion"] = {
                     "BaseRectificada": origin.amount_untaxed,   #abs(origin.amount_untaxed_signed),
                     "CuotaRectificada": abs(
                         origin.amount_total - origin.amount_untaxed
                     ),
               }
        inv_dict.update(
            {
                "DescripcionOperacion": self._get_verifactu_description(),
            }
        )
        if verifactu_doc_type not in ("F2", "R5"):
            inv_dict.update(
                {
                    "Destinatarios": self._get_receiver_dict(),
                }
            )
        elif verifactu_doc_type in ("F2", "R5"):
            inv_dict.update({"FacturaSinIdentifDestinatarioArt61d": "S"})
        #registrationDate = self._get_verifactu_registration_date()
        #raise Warning(registrationDate)
        inv_dict.update(
            {
                "Desglose": taxes_dict,
                "CuotaTotal": amount_tax,
                "ImporteTotal": amount_total,
                "Encadenamiento": self._get_chaining_invoice_dict(),
                "SistemaInformatico": self._get_verifactu_developer_dict(),
                "FechaHoraHusoGenRegistro": self._get_verifactu_registration_date(),
                "TipoHuella": "01",  # SHA-256
                "Huella": self.verifactu_hash,
            }
        )
        if self.verifactu_state == "sent_w_errors":
            inv_dict.update(
                {
                    "Subsanacion": "S",
                    # "RechazoPrevio": "X",
                    "Huella": self._get_subsanation_verifactu_hash(),
                }
            )
        registroAlta.setdefault("RegistroAlta", inv_dict)
        #raise Warning(registroAlta)
        return registroAlta

    def _get_chaining_invoice_dict(self):
        """TODO
        si no es el primer registro, hay que enviar el registro anterior.
        Cuando sepamos cuál es el registro anterior"""
        prev_invoice = self._get_previous_invoice()
        if prev_invoice:
           valores = {
               "RegistroAnterior": {
                    "IDEmisorFactura": prev_invoice._get_verifactu_issuer(),
                    "NumSerieFactura": prev_invoice._get_document_serial_number(),
                    "FechaExpedicionFactura": prev_invoice._change_date_format(
                        prev_invoice._get_document_date()),
                    "Huella": prev_invoice.verifactu_hash, 
               }
           }
           return valores
        else:
           return {"PrimerRegistro": "S"}

    def _get_verifactu_tax_dict(self, tax_line, tax_lines):
        """Get the Verifactu tax dictionary for the passed tax line.
        :param self: Single invoice record.
        :param tax_line: Tax line that is being analyzed.
        :param tax_lines: Dictionary of processed invoice taxes for further operations
            (like REQ).
        :return: A dictionary with the corresponding Verifactu tax values.
        """
        """tax = tax_line["tax"]
        tax_base_amount = tax_line["base"]
        if tax.amount_type == "group":
            tax_type = abs(tax.children_tax_ids.filtered("amount")[:1].amount)
        else:
            tax_type = abs(tax.amount)
        tax_dict = {
            "CalificacionOperacion": str(tax_type),
            "BaseImponibleOimporteNoSujeto": tax_base_amount,
        }"""
        key = "CuotaTotal"
        tax_dict[key] = tax_line["amount"] 
        # Recargo de equivalencia
        req_tax = self._get_verifactu_tax_req(tax)
        if req_tax:
            tax_dict[key] = tax_line["amount"] + tax_lines[req_tax]["amount"]
            #tax_dict["TipoRecargoEquivalencia"] = req_tax.amount
            #tax_dict["CuotaRecargoEquivalencia"] = tax_lines[req_tax]["amount"]
        #raise Warning(tax_dict)
        return tax_dict

    def _get_verifactu_tax_req(self, tax):
        """Get the associated req tax for the specified tax.

        :param self: Single invoice record.
        :param tax: Initial tax for searching for the RE linked tax.
        :return: REQ tax (or empty recordset) linked to the provided tax.
        """
        self.ensure_one()
        document_date = self._get_document_fiscal_date()
        taxes_req = self._get_verifactu_taxes_map(["RE"], document_date)
        re_lines = self.line_ids.filtered(
            lambda x: tax in x.tax_ids and x.tax_ids & taxes_req
        )
        req_tax = []
        req_tax = re_lines.mapped("tax_ids") & taxes_req
        #raise Warning(req_tax)
        #if len(req_tax) > 1:
        #    raise UserError(_("There's a mismatch in taxes for RE. Check them."))
        return req_tax

    def _get_verifactu_taxes_and_total(self):
        self.ensure_one()
        taxes_dict = {}
        taxes_dict.setdefault("DetalleDesglose", [])
        tax_lines = [] #self._get_aeat_tax_info()
        document_date = self._get_document_fiscal_date()
        taxes_S1 = self._get_verifactu_taxes_map(["S1"], document_date)
        taxes_S2 = self._get_verifactu_taxes_map(["S2"], document_date)
        taxes_N1 = self._get_verifactu_taxes_map(["N1"], document_date)
        taxes_N2 = self._get_verifactu_taxes_map(["N2"], document_date)
        taxes_req = self._get_verifactu_taxes_map(["RE"], document_date)
        breakdown_taxes = taxes_S1 + taxes_S2 + taxes_N1 + taxes_N2
        tax_dict = {}
        vtax = []
        for tax_line in self.tax_line: #self.invoice_line:
            #BUSCAR IMPUESTO
            imp = self.env['account.tax'].search([('name', '=', tax_line.name)])
            if imp:
               #for tax_line in inv_line.invoice_line_tax_id:
               if imp in breakdown_taxes:
                   operation_type = self._get_operation_type(
                    imp, taxes_S1, taxes_S2, taxes_N1, taxes_N2
                   )
                   tax_dict = {
                    "Impuesto": self.verifactu_tax_key,
                    "ClaveRegimen": self.verifactu_registration_key_code,
                    "CalificacionOperacion": operation_type,
                    "BaseImponibleOimporteNoSujeto": tax_line.base,
                    "TipoImpositivo": round(imp.amount * 100,2),
                    "CuotaRepercutida": tax_line.amount
                   }
                   #raise Warning(tax_dict)
                   # si es exenta:
                   # "OperacionExenta": "", # TODO
                   #taxes_dict.update(self._get_verifactu_tax_dict(tax_line, tax_lines))
                   #tax_lines.append(tax_dict)
                   #taxes_dict["DetalleDesglose"].append(tax_dict)
                   #key = "CuotaTotal"
                   #taxes_dict[key] = tax_line["amount"]
                   # Recargo de equivalencia
                   """lines = self.tax_line.search([('name', '=', )]) #self.invoice_line.invoice_line_tax_id
                   req = []
                   for re_line in lines:
                       if not re_line.name == tax_line.name:
                          if reqline in taxes_req:
                             tax_dict["TipoRecargoEquivalencia"] = round(reqline.amount * 100,2)
                          #buscar tax_line del recargo
                          #impreq = self.tax_line.filtered(lambda x: x.name == re_line.name)
                          #if impreq:
                          #raise Warning(impreq)
                          tax_dict["CuotaRecargoEquivalencia"] = re_line.amount
                          #raise Warning(re_line)"""
                   #req_tax = []
                   #req_tax = re_lines.mapped("tax_ids") & taxes_req
                   #if req_tax:
                   #   raise Warning(req_tax)
                   #if imp in taxes_req:
                   #   #taxes_dict[key] = invoice_line["amount"] + taxes_lines[req_tax]["amount"]
                   #   tax_dict["TipoRecargoEquivalencia"] = round(imp.amount * 100,2)
                   #   tax_dict["CuotaRecargoEquivalencia"] = tax_line.amount
                   taxes_dict["DetalleDesglose"].append(tax_dict)
        #raise Warning(taxes_dict)
        return (
            taxes_dict,
            self._get_verifactu_amount_tax(),
            self._get_verifactu_amount_total(),
        )


    def _get_operation_type(self, tax_line, taxes_S1, taxes_S2, taxes_N1, taxes_N2):
        """
        S1	Operación Sujeta y No exenta - Sin inversión del sujeto pasivo.
        S2	Operación Sujeta y No exenta - Con Inversión del sujeto pasivo
        N1	Operación No Sujeta artículo 7, 14, otros.
        N2	Operación No Sujeta por Reglas de localización.
        """
        tax = tax_line #["tax"]
        if tax in taxes_S1:
            return "S1"
        elif tax in taxes_S2:
            return "S2"
        elif tax in taxes_N1:
            return "N1"
        elif tax in taxes_N2:
            return "N2"
        return "S1"

    def _get_receiver_dict(self):
        self.ensure_one()
        receiver = self._aeat_get_partner()
        vat_info = receiver.vat[2:] #_parse_aeat_vat_info()
        return {
            "IDDestinatario": {
                "NombreRazon": receiver.name,
                "NIF": receiver.vat[2:], #vat_info[2:0],
                # "IDOtro": {
                #     "IDType": vat_info[1],
                #     "ID": vat_info[0],
                # }
            }
        }

    #CANCELAR FACTURA EN VERIFACTU
    def cancel_verifactu(self):
        raise NotImplementedError

    #OBTENCION QR URL INVOICE AEAT
    def _compute_verifactu_qr_url(self):
        for move in self: #.filtered(lambda m: m.inalterable_hash):
            base_url = move.company_id.url_qrverifactu_test
            #raise Warning(base_url)
            urlqrinvoice = base_url
            if move.company_id.vat:
               urlqrinvoice += "nif=" + move.company_id.vat[2:]
            if move.number:
               urlqrinvoice += "&numserie=" + move.number
            if move.date_invoice:
               urlqrinvoice += "&fecha=" + move._change_date_format(self._get_document_date())
            if move.amount_total:
               urlqrinvoice += "&importe=" + str(move.amount_total)
            move.verifactu_qr_url = urlqrinvoice
            # Generar el código QR
            qr = qrcode.QRCode(
               version=1,  # Tamaño del QR: 1 es el más pequeño
               error_correction=qrcode.constants.ERROR_CORRECT_L,  # Nivel de corrección de errores
               box_size=10,  # Tamaño de los cuadros
               border=4,  # Tamaño del borde
            )
            qr.add_data(urlqrinvoice)
            qr.make(fit=True)
            # Crear una imagen del QR
            qr_image = qr.make_image(fill_color="black", back_color="white")
            # Guardar la imagen en un archivo
            temp_file = StringIO()
            qr_image.save(temp_file)
            qr_image = b64encode(temp_file.getvalue())
            #raise Warning(qr_image)
            move.write({'qr_image': qr_image})
            #raise Warning(move.qr_image)
            #move.qr_image = img.save("codigo_qr.png")


    def _compute_verifactu_macrodata(self):
        for document in self:
            document.verifactu_macrodata = (
                float_compare(
                    abs(document._get_verifactu_amount_total()),
                    VERIFACTU_MACRODATA_LIMIT,
                    precision_digits=2,
                )
                >= 0
            )

    @api.model
    def _get_verifactu_tax_keys(self):
        return self.env["account.fiscal.position"]._get_verifactu_tax_keys()

    def _connect_params_aeat(self, mapping_key):
        self.ensure_one()
        agency = self.company_id.tax_agency_id
        if not agency:
            # We use spanish agency by default to keep old behavior with
            # ir.config parameters. In the future it might be good to reinforce
            # to explicitly set a tax agency in the company by raising an error
            # here.
            agency = self.env.ref("l10n_es_verifactu.aeat_tax_agency_spain")
        return agency._connect_params_verifactu(mapping_key, self.company_id)

    def _get_aeat_header(self, tipo_comunicacion=False, cancellation=False):
        """Builds VERIFACTU send header

        :param tipo_comunicacion String 'A0': new reg, 'A1': modification
        :param cancellation Bool True when the communitacion es for document
            cancellation
        :return Dict with header data depending on cancellation
        """
        self.ensure_one()
        if not self.company_id.vat:
            raise ValidationError(
                _("No VAT configured for the company '{}'").format(self.company_id.name)
            )
        header = {
            "ObligadoEmision": {
                "NombreRazon": self.company_id.name[0:120],
                "NIF": self.company_id.partner_id.vat[2:] #_parse_aeat_vat_info()[2],
            },
        }
        return header

    def _get_verifactu_invoice_dict(self):
        self.ensure_one()
        inv_dict = {}
        mapping_key = self._get_mapping_key()
        if mapping_key in ["out_invoice", "out_refund"]:
            inv_dict = self._get_verifactu_invoice_dict_out(cancel=False)
            #raise Warning(inv_dict)
        else:
            raise NotImplementedError
        """round_by_keys(
            inv_dict,
            [
                "BaseImponibleOimporteNoSujeto",
                "CuotaRepercutida",
                "TipoRecargoEquivalencia",
                "CuotaRecargoEquivalencia",
                "CuotaTotal",
                "ImporteTotal",
                "BaseRectificada",
                "CuotaRectificada",
            ],
        )"""
        #raise Warning(inv_dict)
        return inv_dict

    #def _get_aeat_invoice_dict_out(self, cancel=False):
    #    raise NotImplementedError

    def _get_verifactu_developer_dict(self):
        """
        TODO
        Datos del desarrollador del sistema informático
        """
        return {
            "NombreRazon": _("Asoc Española de Odoo"),
            "NIF": "G87846952",
            "NombreSistemaInformatico": "odoo",
            "IdSistemaInformatico": "11",
            "Version": "1.0",
            "NumeroInstalacion": "1",
            "TipoUsoPosibleSoloVerifactu": "N",
            "TipoUsoPosibleMultiOT": "S",
            "IndicadorMultiplesOT": "S",
            "IDOtro": {
                "IDType": "",
                "ID": "",
            },
        }

    def _get_previous_invoice(self):
        prev_invoice = self.search([('state', 'in', ['open', 'paid']),
        ('company_id', '=', self.company_id.id), ('verifactu_hash', '!=', False), ('verifactu_state', '!=', 'not_sent'), ('create_date', '<', self.create_date)], order='create_date desc')
        if prev_invoice:
           return prev_invoice[0]
        #else:
        #   return []
        #   raise ValidationError(_("No se encuentra factura previa"))

    def _aeat_check_exceptions(self):
        """Inheritable method for exceptions control when sending veri*FACTU invoices."""
        res = super()._aeat_check_exceptions()
        if self.company_id.verifactu_enabled and not self.verifactu_enabled:
            raise UserError(_("This invoice is not veri*FACTU enabled."))
        return res

    def _change_date_format(self, date):
        datetimeobject = fields.Date.from_string(date)
        new_date = datetimeobject.strftime(VERIFACTU_DATE_FORMAT)
        return new_date

    def _compute_verifactu_hash(self):
        # TODO  by the moment those fields are not stored,
        # but they must be stored because are unalterable
        # when invoice is sent to verifactu, because the hash depends
        # on previous sent hash..
        for record in self:
            verifactu_hash_values = record._get_verifactu_hash_string()
            record.verifactu_hash_string = verifactu_hash_values
            hash_string = sha256(verifactu_hash_values.encode("utf-8"))
            record.verifactu_hash = hash_string.hexdigest().upper()


    def _get_verifactu_version(self):
        return VERIFACTU_VERSION

    def _compute_verifactu_refund_type(self):
        self.verifactu_refund_type = False

    def _is_aeat_simplified_invoice(self):
        """Inheritable method to allow control when an
        invoice are simplified or normal"""
        partner = self._aeat_get_partner()
        return partner.aeat_simplified_invoice

    def _get_verifactu_jobs_field_name(self):
        raise NotImplementedError

    @api.multi
    def send_verifactu(self):
        """General public method for filtering out of the starting recordset the records
        that shouldn't be sent to Verifactu:

        - Documents of companies with Verifactu not enabled (through verifactu_enabled).
        - Documents not applicable to be sent to Verifactu (through verifactu_enabled).
        - Documents in non applicable states (for example, cancelled invoices).
        - Documents already sent to Verifactu.
        - Documents with sending jobs pending to be executed.
        """
        #regenerar hash por si ha habido algún envío de una nueva factura.
        #self._get_verifactu_hash_string()

        valid_states = self._get_valid_document_states()
        for document in self:
            """if (
                not document.verifactu_enabled
                or document.state not in valid_states
                or document.verifactu_state in ["sent", "cancelled"]
            ):
                continue"""
            document._process_verifactu_send()

    def _process_verifactu_send(self):
        """
        Process document sending to Verifactu
        TODO : use connector
        """
        for record in self:
            record.confirm_verifactu_one_document()

    def confirm_verifactu_one_document(self):
        self.sudo()._send_document_to_verifactu()

    def _send_document_to_verifactu(self):
        for document in self.filtered(
            lambda i: i.state in self._get_valid_document_states()
        ):
            if document.verifactu_state == "not_sent":
                tipo_comunicacion = "A0"
            else:
                tipo_comunicacion = "A1"
            header = document._get_aeat_header(tipo_comunicacion)
            doc_vals = {
                "verifactu_header_sent": json.dumps(header, indent=4),
            }
            try: 
                inv_dict = document._get_verifactu_invoice_dict()
                #raise Warning(inv_dict)
            except Exception as fault:
                raise ValidationError(fault) #from fault
            try:
                mapping_key = document._get_mapping_key()
                serv = document._connect_verifactu(mapping_key)
                #raise Warning(serv)
                #doc_vals["verifactu_content_sent"] = json.dumps(inv_dict, indent=4)
                if mapping_key in ["out_invoice", "out_refund"]:
                    res = serv.RegFactuSistemaFacturacion(header, inv_dict)
                res_line = res["RespuestaLinea"][0]
                if res["EstadoEnvio"] == "Correcto":
                    doc_vals.update(
                        {
                            "verifactu_state": "sent",
                            "verifactu_csv": res["CSV"],
                            "verifactu_send_failed": False,
                        }
                    )
                elif (
                    res["EstadoEnvio"] == "ParcialmenteCorrecto"
                    and res_line["EstadoRegistro"] == "AceptadoConErrores"
                ):
                    doc_vals.update(
                        {
                            "verifactu_state": "sent_w_errors",
                            "verifactu_csv": res["CSV"],
                            "verifactu_send_failed": True,
                        }
                    )
                else:
                    doc_vals["aeat_send_failed"] = True
                doc_vals["verifactu_return"] = res
                send_error = False
                if res_line["CodigoErrorRegistro"]:
                    send_error = "{} | {}".format(
                        str(res_line["CodigoErrorRegistro"]),
                        str(res_line["DescripcionErrorRegistro"]),
                    )
                doc_vals["verifactu_send_error"] = send_error
                document.write(doc_vals)
            except Exception as fault:
                new_cr = Registry(self.env.cr.dbname).cursor()
                env = api.Environment(new_cr, self.env.uid, self.env.context)
                document = env[document._name].browse(document.id)
                doc_vals.update(
                    {
                        "verifactu_send_failed": True,
                        "verifactu_send_error": repr(fault)[:200],
                        "verifactu_return": repr(fault),
                        "verifactu_content_sent": json.dumps(inv_dict, indent=4),
                    }
                )
                document.write(doc_vals)
                new_cr.commit()
                new_cr.close()
                raise ValidationError(fault)

    def _connect_verifactu(self, mapping_key):
        # de momento no puedo el _connect_aeat del aeat_mixin porque si no pongo
        # forbid_entities en settings del Client da error de entities forbiden
        self.ensure_one()
        public_crt = self.env['ir.config_parameter'].get_param(
            'l10n_es_aeat_verifactu.publicCrt', False)
        private_key = self.env['ir.config_parameter'].get_param(
            'l10n_es_aeat_verifactu.privateKey', False)
        params = self._connect_params_aeat(mapping_key)
        parser = etree.XMLParser(resolve_entities=False)
        session = Session()
        session.cert = (public_crt, private_key)
        transport = Transport(session=session) #, xml_headers={'Content-Type': 'application/soap+xml'})
        history = HistoryPlugin()
        settings = Settings(forbid_entities=False)
        wsdl=params["wsdl"]
        client = Client(
            wsdl=params["wsdl"],
            transport=transport,
            plugins=[history],
            settings=settings,
        )
        return self._bind_service(client, params["port_name"], params["address"])

    def _bind_service(self, client, port_name, address=None):
        self.ensure_one()
        service = client._get_service("sfVerifactu")
        port = client._get_port(service, port_name)
        address = address or port.binding_options["address"]
        return client.create_service(port.binding.name, address)

    @api.multi
    def map_verifactu_tax_template(self, tax_template, mapping_taxes):
        """Adds a tax template -> tax id to the mapping.
        Adapted from account_chart_update module.

        :param self: Single invoice record.
        :param tax_template: Tax template record.
        :param mapping_taxes: Dictionary with all the tax templates mapping.
        :return: Tax template current mapping
        """
        self.ensure_one()
        if not tax_template:
            return self.env['account.tax']
        if mapping_taxes.get(tax_template):
            return mapping_taxes[tax_template]
        # search inactive taxes too, to avoid re-creating
        # taxes that have been deactivated before
        tax_obj = self.env['account.tax'].with_context(active_test=False)
        criteria = ['|',
                    ('name', '=', tax_template.name),
                    ('description', '=', tax_template.name)]
        if tax_template.description:
            criteria = ['|'] + criteria
            criteria += [
                '|',
                ('description', '=', tax_template.description),
                ('name', '=', tax_template.description),
            ]
        criteria += [('company_id', '=', self.company_id.id)]
        mapping_taxes[tax_template] = tax_obj.search(criteria)
        return mapping_taxes[tax_template]


    @api.model
    def _get_verifactu_taxes_map(self, codes, date):
        """Return the codes that correspond to verifactu map line codes.

        :param codes: List of code strings to get the mapping.
        :param date: Date to map
        :return: Recordset with the corresponding codes
        """
        map_obj = self.env["aeat.verifactu.map"].sudo().with_context(active_test=False)
        taxes = self.env['account.tax']
        verifactu_map = map_obj.search(
            [
                "|",
                ("date_from", "<=", date),
                ("date_from", "=", False),
                "|",
                ("date_to", ">=", date),
                ("date_to", "=", False),
            ],
            limit=1,
        )
        mapping_taxes = {}
        tax_templates = verifactu_map.map_lines.filtered(
            lambda x: x.code in codes
        ).taxes
        for tax_template in tax_templates:
            taxes += self.map_verifactu_tax_template(tax_template, mapping_taxes)
        return taxes

    @api.depends("fiscal_position")
    def _compute_verifactu_tax_key(self):
        for document in self:
            document.verifactu_tax_key = (
                document.fiscal_position.verifactu_tax_key or "01"
            )

    @api.depends("fiscal_position")
    def _compute_verifactu_registration_key(self):
        for document in self:
            if document.fiscal_position:
                key = document.fiscal_position.verifactu_registration_key
                if key:
                    document.verifactu_registration_key = key
            else:
                domain = [
                    ("code", "=", "01"),
                    (
                        "verifactu_tax_key",
                        "=",
                        "iva",
                    ),
                ]
                verifactu_key_obj = self.env["aeat.verifactu.registration.keys"]
                document.verifactu_registration_key = verifactu_key_obj.search(
                    domain, limit=1
                )

    @api.depends("verifactu_registration_key")
    def _compute_verifactu_registration_key_code(self):
        for record in self:
            record.verifactu_registration_key_code = (
                record.verifactu_registration_key.code
            )
